#!/usr/bin/env python

import numpy as np
import psana
import time
from datetime import datetime
begin_job_time = datetime.now().strftime('%m/%d/%Y %H:%M:%S')
import argparse
import socket
import os
import logging 
import requests
import sys
from glob import glob
from PIL import Image
from requests.auth import HTTPBasicAuth

########################################################## 
##
## User Input start --> 
##
########################################################## 
##########################################################
# functions for run dependant parameters
##########################################################
def getROIs(run):
    """ Will only write the results of the ROIs analysis to file 
    """
    if isinstance(run,basestring):
        run=int(run)
    roiDict={}
    if run <= 9:
        roiDict['jungfrau1M'] = [ [[0,1], [1,74], [312,381]],
                                  [[1,2], [8,89], [218,303]] ]
    else:
        roiDict['jungfrau1M'] = [ [[0,1], [1,74], [312,381]],
                                  [[1,2], [8,89], [218,303]] ]

    return roiDict


def getROIs_write(run):
    """ Will also write the ROIs area to file
    """
    if isinstance(run,basestring):
        run=int(run)
    roiDict={}
    if run <= 2:
        return roiDict
    elif run>312:
        roiDict['jungfrau1M'] = [ [[0,1], [157,487], [294,598]]]
    else:
        roiDict['jungfrau1M'] = [ [[0,1], [26,245], [330,487]]]
#     if run in other_det_run:
#         roiDict['other_det'] = [ [[100,200],[100,200]]  ]
#     else:
#         roiDict['other_det'] = []
    return roiDict


def getAzIntParams(run):
    if isinstance(run,basestring):
        run=int(run)
        
    ret_dict = {'eBeam': 18.0}
    ret_dict['center'] = [87526.79161840, 92773.3296889500]
    ret_dict['dis_to_sam'] = 80.
    return {}
    #return ret_dict

    
def define_dets(run):
    dets=[]

    ROIs = getROIs(run)
    ROIs_write = getROIs_write(run)
    detnames = ['jungfrau1M']
#     detnames=['jungfrau1M','opal_0','opal_2']
    for detname in detnames:
        havedet = checkDet(ds.env(), detname)
        if havedet:
            if detname=='jungfrau1M':
                #common_mode=71 #also try 71
                common_mode=0
            else:
                common_mode=0 #no common mode
            det = DetObject(detname ,ds.env(), int(run), common_mode=common_mode)
            #check for ROIs:
            if detname in ROIs:
                for iROI,ROI in enumerate(ROIs[detname]):
                    det.addFunc(ROIFunc(ROI=ROI, name='ROI_%d'%iROI))
            if detname in ROIs_write:
                for iROI,ROI in enumerate(ROIs_write[detname]):
                    det.addFunc(ROIFunc(ROI=ROI, name='ROIw_%d'%iROI, writeArea=True))

            det.storeSum(sumAlgo='calib')
            dets.append(det)
    return dets



##########################################################
# run independent parameters 
##########################################################
#aliases for experiment specific PVs go here
#epicsPV = ['slit_s1_hw'] 
epicsPV = []
#fix timetool calibration if necessary
#ttCalib=[0.,2.,0.]
ttCalib=[]
#ttCalib=[1.860828, -0.002950]
#decide which analog input to save & give them nice names
#aioParams=[[1],['laser']]
aioParams=[]
########################################################## 
##
## <-- User Input end
##
##########################################################

##########################################################
# Custom exception handler to make job abort if a single rank fails
# Avoid jobs hanging forever and report actual error message to the log file
import traceback as tb

def global_except_hook(exctype, value, exc_traceback):
    tb.print_exception(exctype, value, exc_traceback)
    sys.stderr.write("except_hook. Calling MPI_Abort().\n")
    sys.stdout.flush() # Command to flush the output - stdout
    sys.stderr.flush() # Command to flush the output - stderr
    # Note: mpi4py must be imported inside exception handler, not globally.     
    import mpi4py.MPI
    mpi4py.MPI.COMM_WORLD.Abort(1)
    sys.__excepthook__(exctype, value, exc_traceback)
    return

sys.excepthook = global_except_hook
##########################################################


# General Workflow
# This is meant for arp which means we will always have an exp and run
# Check if this is a current experiment
# If it is current, check in ffb for xtc data, if not there, default to psdm

fpath=os.path.dirname(os.path.abspath(__file__))
fpathup = '/'.join(fpath.split('/')[:-1])
sys.path.append(fpathup)
print(fpathup)

from smalldata_tools.utilities import printMsg, checkDet
from smalldata_tools.SmallDataUtils import setParameter, getUserData, getUserEnvData
from smalldata_tools.SmallDataUtils import defaultDetectors, detData
from smalldata_tools.SmallDataDefaultDetector import epicsDetector, eorbitsDetector
from smalldata_tools.SmallDataDefaultDetector import bmmonDetector, ipmDetector
from smalldata_tools.SmallDataDefaultDetector import encoderDetector, adcDetector
from smalldata_tools.DetObject import DetObject
from smalldata_tools.roi_rebin import ROIFunc, spectrumFunc, projectionFunc, sparsifyFunc, imageFunc
from smalldata_tools.waveformFunc import getCMPeakFunc, templateFitFunc
from smalldata_tools.droplet import dropletFunc
from smalldata_tools.photons import photonFunc
from smalldata_tools.azimuthalBinning import azimuthalBinning

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Constants
HUTCHES = [
	'AMO',
	'SXR',
	'XPP',
	'XCS',
	'MFX',
	'CXI',
	'MEC',
	'DIA'
]

FFB_BASE = '/cds/data/drpsrcf'
PSDM_BASE = '/reg/d/psdm'
SD_EXT = '/hdf5/smalldata'

# Define Args
parser = argparse.ArgumentParser()
parser.add_argument('--run', help='run', type=str, default=os.environ.get('RUN_NUM', ''))
parser.add_argument('--experiment', help='experiment name', type=str, default=os.environ.get('EXPERIMENT', ''))
parser.add_argument('--stn', help='hutch station', type=int, default=0)
parser.add_argument('--nevents', help='number of events', type=int, default=1e9)
parser.add_argument('--directory', help='directory for output files (def <exp>/hdf5/smalldata)')
parser.add_argument('--gather_interval', help='gather interval', type=int, default=100)
parser.add_argument('--norecorder', help='ignore recorder streams', action='store_true', default=False)
parser.add_argument('--url', default="https://pswww.slac.stanford.edu/ws-auth/lgbk/")
parser.add_argument('--epicsAll', help='store all epics PVs', action='store_true', default=False)
parser.add_argument('--full', help='store all data (please think before usig this)', action='store_true', default=False)
parser.add_argument('--default', help='store only minimal data', action='store_true', default=False)
parser.add_argument('--image', help='save everything as image (use with care)', action='store_true', default=False)
parser.add_argument('--tiff', help='save all images also as single tiff (use with even more care)', action='store_true', default=False)
parser.add_argument("--postRuntable", help="postTrigger for seconday jobs", action='store_true', default=True)
parser.add_argument("--wait", help="wait for a file to appear", action='store_true', default=False)
args = parser.parse_args()
logger.debug('Args to be used for small data run: {0}'.format(args))

###### Helper Functions ##########

def get_xtc_files(base, hutch, run):
	"""File all xtc files for given experiment and run"""
	run_format = ''.join(['r', run.zfill(4)])
	data_dir = ''.join([base, '/', hutch.lower(), '/', exp, '/xtc'])
	xtc_files = glob(''.join([data_dir, '/', '*', '-', run_format, '*']))

	return xtc_files

def get_sd_file(write_dir, exp, hutch):
	"""Generate directory to write to, create file name"""
	if write_dir is None:
                if useFFB:
		    write_dir = ''.join([FFB_BASE, '/', hutch, '/', exp, '/scratch', SD_EXT])
                else:
                    write_dir = ''.join([PSDM_BASE, '/', hutch, '/', exp, SD_EXT])
        if args.default:
            if useFFB:
                write_dir = write_dir.replace('hdf5','hdf5_def')
            else:
                write_dir = write_dir.replace('hdf5','scratch')

	h5_f_name = ''.join([write_dir, '/', exp, '_Run', run.zfill(4), '.h5'])
	if not os.path.isdir(write_dir):
		logger.debug('{0} does not exist, creating directory'.format(write_dir))
		try:
			os.mkdir(write_dir)
		except OSError as e:
			logger.debug('Unable to make directory {0} for output, exiting: {1}'.format(write_dir, e))
			sys.exit()

	logger.debug('Will write small data file to {0}'.format(h5_f_name))
	return h5_f_name

##### START SCRIPT ########

# Define hostname
hostname = socket.gethostname()

# Parse hutch name from experiment and check it's a valid hutch
exp = args.experiment
run = args.run
station = args.stn
logger.debug('Analyzing data for EXP:{0} - RUN:{1}'.format(args.experiment, args.run))

begin_prod_time = datetime.now().strftime('%m/%d/%Y %H:%M:%S')

hutch = exp[:3].upper()
if hutch not in HUTCHES:
	logger.debug('Could not find {0} in list of available hutches'.format(hutch))
	sys.exit()	

xtc_files = []
# If experiment matches, check for files in ffb
useFFB=False
#with the new FFB, no need to check both on & offline as systems are independant.
if hostname.find('drp')>=0:
    nFiles=0
    logger.debug('On FFB')
    waitFilesStart=datetime.now()
    while nFiles==0:
        xtc_files = get_xtc_files(FFB_BASE, hutch, run)
        print (xtc_files)
        nFiles = len(xtc_files)
        if nFiles == 0:
            if not args.wait:
                print('We have no xtc files for run %s in %s in the FFB system, we will quit')
                sys.exit()
            else:
                print('We have no xtc files for run %s in %s in the FFB system, we will wait for 10 second and check again.'%(run,exp))
                time.sleep(10)
    waitFilesEnd=datetime.now()
    print('Files appeared after %s seconds'%(str(waitFilesStart-waitFilesEnd)))
    useFFB = True

# If not a current experiment or files in ffb, look in psdm
else:
    logger.debug('Not on FFB, use offline system')
    xtc_files = get_xtc_files(PSDM_BASE, hutch, run)
    if len(xtc_files)==0:
        print('We have no xtc files for run %s in %s in the offline system'%(run,exp))
        sys.exit()

# Get output file, check if we can write to it
h5_f_name = get_sd_file(args.directory, exp, hutch)
#if args.default:
#    if useFFB:
#        h5_f_name = h5_f_name.replace('hdf5','hdf5_def')
#    else:
#        h5_f_name = h5_f_name.replace('hdf5','scratch')

# Define data source name and generate data source object, don't understand all conditions yet
ds_name = ''.join(['exp=', exp, ':run=', run, ':smd:live'])
#ds_name = ''.join(['exp=', exp, ':run=', run, ':smd'])
if args.norecorder:
        ds_name += ':stream=0-79'
if useFFB:
        ds_name += ':dir=/cds/data/drpsrcf/%s/%s/xtc'%(exp[0:3],exp)
# try this: live & all streams (once I fixed the recording issue)
#ds_name = ''.join(['exp=', exp, ':run=', run, ':smd:live'])
try:
    ds = psana.MPIDataSource(ds_name)
except Exception as e:
    logger.debug('Could not instantiate MPIDataSource with {0}: {1}'.format(ds_name, e))
    sys.exit()

# Generate smalldata object
small_data = ds.small_data(h5_f_name, gather_interval=args.gather_interval)

# Not sure why, but here
if ds.rank is 0:
	logger.debug('psana conda environment is {0}'.format(os.environ['CONDA_DEFAULT_ENV']))

########################################################## 
##
## Setting up the default detectors
##
########################################################## 
default_dets = defaultDetectors(hutch.lower())


#
# add stuff here to save all EPICS PVs.
#
if args.full or args.epicsAll:
    logger.debug('epicsStore names', ds.env().epicsStore().pvNames())
    if args.experiment.find('dia')>=0:
        epicsPV=ds.env().epicsStore().pvNames()
    else:
        epicsPV=ds.env().epicsStore().aliases()
    if len(epicsPV)>0:
        logger.debug('adding all epicsPVs....')
        default_dets.append(epicsDetector(PVlist=epicsPV, name='epicsAll'))
default_dets.append(eorbitsDetector())
default_det_aliases = [det.name for det in default_dets]

if not args.default:
    dets = define_dets(args.run)
else:
    dets = []

det_presence={}
if args.full:
    aliases = []
    for dn in psana.DetNames():
        if dn[1]!='':
            aliases.append(dn[1])
        else:
            aliases.append(dn[0])

    for alias in aliases:
        det_presence[alias]=1
        if alias in default_det_aliases: continue
        if alias=='FEEGasDetEnergy': continue #done by mpidatasource
        if alias=='PhaseCavity':     continue #done by mpidatasource
        if alias=='EBeam':           continue #done by mpidatasource
        if alias.find('evr')>=0:     continue #done by mpidatasource
        if alias=='ControlData':     continue #done by my code
        #done in standard default detectors.
        if alias=='HX2-SB1-BMMON' and args.experiment.find('xpp')>=0:  continue
        if alias=='XPP-SB2-BMMON' and args.experiment.find('xpp')>=0:  continue
        if alias=='XPP-SB3-BMMON' and args.experiment.find('xpp')>=0:  continue
        if alias=='XPP-USB-ENCODER-02' and args.experiment.find('xpp')>=0:  continue
        if alias=='XCS-SB1-BMMON' and args.experiment.find('xcs')>=0:  continue
        if alias=='XCS-SB2-BMMON' and args.experiment.find('xcs')>=0:  continue
        if alias=='MEC-XT2-BMMON-02' and args.experiment.find('mec')>=0:  continue
        if alias=='MEC-XT2-BMMON-03' and args.experiment.find('mec')>=0:  continue

        if alias.find('BMMON')>=0:
            print('append a bmmon',alias)
            default_dets.append(bmmonDetector(alias))
            continue
        elif alias.find('IPM')>=0 or alias.find('Ipm')>0:
            default_dets.append(ipmDetector(alias, savePos=True))
            continue
        elif alias.find('DIO')>=0 or alias.find('Imb')>0:
            default_dets.append(ipmDetector(alias, savePos=False))
            continue
        elif alias.find('USB')>=0:
            default_dets.append(encoderDetector(alias))
            continue
        elif alias.find('adc')>=0:
            default_dets.append(adcDetector(alias))
            continue
        try:
            thisDet = DetObject(alias, ds.env(), int(run), name=alias)
            hasGeom=False
            for keyword in ['cs','Cs','epix','Epix','jungfrau','Jungfrau']:
                if alias.find(keyword)>=0 and args.image: hasGeom=True
            if hasGeom:
                fullROI=ROIFunc()
                fullROI.addFunc(imageFunc(coords=['x','y']))
            else:    
                fullROI = ROIFunc(writeArea=True)
            thisDet.addFunc(fullROI)
            dets.append(thisDet)
        except:
            pass

userDataCfg={}
for det in default_dets:
    userDataCfg[det.name] = det.params_as_dict()
for det in dets:
    try:
        userDataCfg[det._name] = det.params_as_dict()
    except:
        userDataCfg[det.name] = det.params_as_dict()
Config={'UserDataCfg':userDataCfg}
small_data.save(Config)

if args.tiff:
    dirname = '/reg/d/psdm/%s/%s/scratch/run%d'%(args.experiment[:3],args.experiment,int(args.run))
    if not os.path.isdir(dirname):
        os.makedirs(dirname)

max_iter = args.nevents / ds.size
for evt_num, evt in enumerate(ds.events()):
    if evt_num > max_iter:
        break

    det_data = detData(default_dets, evt)
    small_data.event(det_data)

    #detector data using DetObject 
    userDict = {}
    for det in dets:
        try:
            #this should be a plain dict. Really.
            det.getData(evt)
            det.processFuncs()
            userDict[det._name]=getUserData(det)
            #print('userdata ',det)
            try:
                envData=getUserEnvData(det)
                if len(envData.keys())>0:
                    userDict[det._name+'_env']=envData
            except:
                pass
            det.processSums()
            #print userDict[det._name]
        except:
            pass
    small_data.event(userDict)

    if args.tiff:
        for key in userDict:
            for skey in userDict[key]:
                if skey.find('area')>=0 or skey.find('img')>=0:
                   if len(userDict[key][skey].shape)==2:
                       im = Image.fromarray(userDict[key][skey])
                       tiff_file = dirname+'/Run_%d_evt_%d_%s.tiff'%(int(args.run), evt_num+1, key)
                       im.save(tiff_file)

    #here you can add any data you like: example is a product of the maximumof two area detectors.
    #try:
    #    jungfrau1MMax = jungfrau1M.evt.dat.max()
    #    epix_vonHamosMax = epix_vonHamos.evt.dat.max()
    #    combDict = {'userValue': jungfrau1MMax*epix_vonHamosMax}
    #    small_data.event(combDict)
    #except:
    #    pass


    #the ARP will pass run & exp via the enviroment, if I see that info, the post updates
    if (int(os.environ.get('RUN_NUM', '-1')) > 0) and ((evt_num<100&evt_num%10==0) or (evt_num<1000&evt_num%100==0) or (evt_num%1000==0)):
        if ds.size == 1:
            requests.post(os.environ["JID_UPDATE_COUNTERS"], json=[{"key": "<b>Current Event</b>", "value": evt_num+1}])
        elif ds.rank == 0:
            requests.post(os.environ["JID_UPDATE_COUNTERS"], json=[{"key": "<b>Current Event / rank </b>", "value": evt_num+1}])

sumDict={'Sums': {}}
for det in dets:
    for key in det.storeSum().keys():
        sumData=small_data.sum(det.storeSum()[key])
        sumDict['Sums']['%s_%s'%(det._name, key)]=sumData
if len(sumDict['Sums'].keys())>0:
    small_data.save(sumDict)

end_prod_time = datetime.now().strftime('%m/%d/%Y %H:%M:%S')

#finishing up here....
logger.debug('rank {0} on {1} is finished'.format(ds.rank, hostname))
small_data.save()
if (int(os.environ.get('RUN_NUM', '-1')) > 0):
    if ds.size > 1 and ds.rank == 0:
        requests.post(os.environ["JID_UPDATE_COUNTERS"], json=[{"key": "<b>Last Event</b>", "value": "~ %d cores * %d evts"%(ds.size,evt_num)}])
    else:
        requests.post(os.environ["JID_UPDATE_COUNTERS"], json=[{"key": "<b>Last Event</b>", "value": evt_num}])
logger.debug('Saved all small data')

if args.postRuntable and ds.rank==0:
    print('posting to the run tables.')
    locStr=''
    if useFFB:
        locStr='_ffb'
    runtable_data = {"Prod%s_end"%locStr:end_prod_time,
                     "Prod%s_start"%locStr:begin_prod_time,
                     "Prod%s_jobstart"%locStr:begin_job_time,
                     "Prod%s_ncores"%locStr:ds.size}
    if args.default:
        runtable_data["SmallData%s"%locStr]="default"
    else:
        runtable_data["SmallData%s"%locStr]="done"
    time.sleep(5)
    ws_url = args.url + "/run_control/{0}/ws/add_run_params".format(args.experiment)
    print('URL:',ws_url)
    #krbheaders = KerberosTicket("HTTP@" + urlparse(ws_url).hostname).getAuthHeaders()
    #r = requests.post(ws_url, headers=krbheaders, params={"run_num": args.run}, json=runtable_data)
    user=(args.experiment[:3]+'opr').replace('dia','mcc')
    with open('/cds/home/opr/%s/forElogPost.txt'%user,'r') as reader:
        answer = reader.readline()
    r = requests.post(ws_url, params={"run_num": args.run}, json=runtable_data, auth=HTTPBasicAuth(args.experiment[:3]+'opr', answer[:-1]))
    print(r)
    if det_presence!={}:
        rp = requests.post(ws_url, params={"run_num": args.run}, json=det_presence, auth=HTTPBasicAuth(args.experiment[:3]+'opr', answer[:-1]))
        print(rp)

# Debug stuff
# How to implement barrier from ds?
if ds.rank == 0:
#    time.sleep(60)#ideally should use barrier or so to make sure all cores have fnished.
    if 'temp' in h5_f_name: # only for hanging job investigation (to be deleted later)
        h5_f_name_2 = get_sd_file(None, exp, hutch)
        os.rename(h5_f_name, h5_f_name_2)
        logger.debug('Move file from temp directory')
