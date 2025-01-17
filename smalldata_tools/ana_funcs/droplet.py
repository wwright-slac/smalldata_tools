import numpy as np
import time
import scipy.ndimage.measurements as measurements
import skimage.measure as measure
import scipy.ndimage.filters as filters
from scipy import sparse
from smalldata_tools.DetObject import DetObjectFunc

class dropletFunc(DetObjectFunc):
    """ 
    threshold : # (noise sigma for) threshold (def: 10.0)
    thresholdLow : # (noise sigma for) lower threshold (def: 3.0): this is to make the spectrum sharper, but not find peaks out of pixels with
                   low significance
    mask (def:None): pass a mask in here, is None: use mask stored in DetObject
    name (def:'droplet'): name used in hdf5 for data
    thresADU (def: 10): ADU threshold for droplets to be further processed
    useRms (def True): if True, threshold and thresholdLow are # of rms of data, otherwise ADU are used.
    relabel (def True): after initial droplet finding and allowing pixels above the lower threshold, relabel the image (so that droplets merge)

    by default, only total number of droplets is returned by process(data)
           Many more information about the droplets ca be saved there.
    """
    def __init__(self, **kwargs):
        self._name = kwargs.get('name', 'droplet')
        super(dropletFunc, self).__init__(**kwargs)
        self.threshold = kwargs.get('threshold',10.)
        self.thresholdLow = kwargs.get('thresholdLow', 3.)
        self.thresADU = kwargs.get('thresADU',10.)
        self.useRms = kwargs.get('useRms', True)
        self.relabel = kwargs.get('relabel',True)
        self._mask = kwargs.get('mask',None)
        #new way to store this info
        self._debug = False
        self.footprint = np.array([[0,1,0],[1,1,1],[0,1,0]])
        self._footprint2d = np.array([[0,1,0],[1,1,1],[0,1,0]])
        self._saveDrops = None
        self._flagMasked = None
        self._needProps = None
        self._nMaxPixels = 15

    def setFromDet(self, det):
        super(dropletFunc, self).setFromDet(det)
        if self._mask is None and det.mask is not None:
            setattr(self, '_mask', det.mask.astype(np.uint8))
        setattr(self, '_rms', det.rms)
        setattr(self, '_needsGeo', det._needsGeo)
        if not self.useRms:
            self._compData = np.ones_like(self._mask)
        else:
            if (len(self._rms.shape) > len(self._mask.shape)):
                self._compData = self._rms[0]
            else:
                self._compData = self._rms
        #self._grid = np.meshgrid(range(max(self._compData.shape)),range(max(self._compData.shape)))
        if len(det.ped.shape)>2:
            self.footprint = np.array([ [[0,0,0],[0,0,0],[0,0,0]], [[0,1,0],[1,1,1],[0,1,0]],  [[0,0,0],[0,0,0],[0,0,0]] ])


    def applyThreshold(self,img, donut=False, invert=False, low=False):
        if not donut:
            if low:
                    threshold = self.thresholdLow
            else:
                    threshold = self.threshold
            if not invert:
                img[img<self._compData*threshold] = 0.0
            else:
                img[img>self._compData*threshold] = 0.0
        else:
            img[img<self._compData*self.thresholdLow] = 0.0
            img[img>self._compData*self.threshold] = 0.0

    def neighborImg(self,img):
        return filters.maximum_filter(img,footprint=self._footprint2d)

    def prepareImg(self,img,donut=False,invert=False, low=False):
        imgIn = img.copy()
        if self._mask is not None:
            imgIn[self._mask==0]=0
        self.applyThreshold(imgIn,donut,invert,low)
        return imgIn

    def returnEmpty(self):
        ret_dict = {'nDroplets': -1}
        for dropSave in self.dropletSaves:
                dropDict = dropSave.initArray()
                for key in dropDict.keys():
                        ret_dict[key] = dropDict[key]
        if self.photonize:
                ret_dict['nPhotons'] = -1
                ret_dict['photons'] = np.zeros([self.maxPhotons,2])     
        return ret_dict


    def process(self, data):
        ret_dict=self.dropletize(data)

        subfuncResults = self.processFuncs()
        for k in subfuncResults:
            for kk in subfuncResults[k]:
                ret_dict['%s_%s'%(k,kk)] = subfuncResults[k][kk]
        return ret_dict

    def dropletize(self, data):
        tstart=time.time()
        if data is None:
            print('img is None!')
            self.ret_dict = self.returnEmpty()
            return
        time_start = time.time()
        img = self.prepareImg(data)
        #is faster than measure.label(img, connectivity=1)
        img_drop = measurements.label(img, structure=self.footprint)
        time_label = time.time()
        #get all neighbors

        if (self.threshold != self.thresholdLow):
            if (len(img_drop[0].shape) == 2):
                imgDrop = self.neighborImg(img_drop[0])
            else:
                imgDrop = np.array([self.neighborImg(imgd_tile) for imgd_tile in img_drop[0]])
            img = self.prepareImg(data, low=True)
            #
            if self.relabel:
                    imgDrop[img==0]=0
                    img_drop_relabel = measurements.label(imgDrop, structure=self.footprint)
                    imgDrop = img_drop_relabel[0]
        else:
            imgDrop = img_drop[0]

        drop_ind = np.arange(1,img_drop[1]+1)
        ret_dict = {'nDroplets_all': len(drop_ind)} # number of droplets before ADU cut.
        adu_drop = measurements.sum(img,imgDrop, drop_ind)
        tfilled=time.time()

        #clean list with lower threshold. Only that one!
        vThres = np.where(adu_drop<self.thresADU)[0]
        vetoed = np.in1d(imgDrop.ravel(), (vThres+1)).reshape(imgDrop.shape)
        imgDrop[vetoed]=0
        drop_ind_thres = np.delete(drop_ind,vThres)

        ret_dict['nDroplets'] = len(drop_ind_thres)
        if self._flagMasked is None and self._needProps is None and self._saveDrops is None:
            for sfunc in [getattr(self, k) for k in  self.__dict__ if isinstance(self.__dict__[k], DetObjectFunc)]:
                self._saveDrops = True
                self._flagMasked = getattr(sfunc, '_flagMasked', self._flagMasked)
                self._needProps = getattr(sfunc, '_needProps', self._needProps)
            if self._saveDrops is None: self._saveDrops = False
            if self._flagMasked is None: self._flagMasked = False
            if self._needProps is None: self._needProps = False

        if not self._saveDrops:
            return ret_dict

        ###
        # add label_img_neighbor w/ mask as image -> sum ADU , field "masked" (binary)?
        ###
        #adu_drop = np.delete(adu_drop,vThres)
        pos_drop = []
        moments = []
        bbox = []
        adu_drop = []
        npix_drop = []
        images = []
        #use region props - this is not particularly performant on busy data.
        #if no information other than adu, npix & is requested in _any_ dropletSave, then to back to old code.
        #<checking like for flagmask>
        #<old code> -- check result against new code.
        #if not '_needProps' in self.__dict__keys():
        if not self._needProps:
            #not sure why I'm not using imgNpix for npix calculation
            #imgNpix = img.copy(); imgNpix[img>0]=1
            #drop_npix = (measurements.sum(imgNpix,imgDrop, drop_ind_thres)).astype(int)
            ##drop_npix = (measurements.sum(img.astype(bool).astype(int),imgDrop, drop_ind_thres)).astype(int)
            drop_adu = np.array(measurements.sum(img,imgDrop, drop_ind_thres))
            #drop_pos = np.array(measurements.center_of_mass(img,imgDrop, drop_ind_thres))
            #adu_drop = np.delete(adu_drop,vThres)
            pos_drop = np.array(measurements.center_of_mass(img,imgDrop, drop_ind_thres))
            npix_drop = (measurements.sum(img.astype(bool).astype(int),imgDrop, drop_ind_thres)).astype(int)
            dat_dict={'data': drop_adu}#adu_drop}
            dat_dict['npix']=npix_drop
            if drop_adu.shape[0]==0:
                dat_dict['row']=np.array([])
                dat_dict['col']=np.array([])
                if self._needsGeo:
                    dat_dict['tile']=np.array([])
            else:
                dat_dict['row']=pos_drop[:,pos_drop.shape[1]-2]
                dat_dict['col']=pos_drop[:,pos_drop.shape[1]-1]
                dat_dict['tile']=pos_drop[:,0]
        else:
            #this should be tested for tiled detectors!
            #t2 = time.time()
            self.regions = measure.regionprops(imgDrop, intensity_image=img, cache=True)
            dropSlices = measurements.find_objects(imgDrop)
            for droplet,ds in zip(self.regions,dropSlices):
                pos_drop.append(droplet['weighted_centroid'])
                moments.append(droplet['weighted_moments_central'])
                bbox.append(droplet['bbox'])
                adu_drop.append(droplet['intensity_image'].sum())
                npix_drop.append((droplet['intensity_image']>0).sum())
                #self._nMaxPixels = 15
                pixelArray = droplet['intensity_image'].flatten()
                if pixelArray.shape[0]>self._nMaxPixels:
                    images.append(pixelArray[:self._nMaxPixels])
                else:
                    images.append(np.append(pixelArray, np.zeros(self._nMaxPixels-pixelArray.shape[0])))
            dat_dict={'data': np.array(adu_drop)}
            dat_dict['npix']=np.array(npix_drop)
            dat_dict['bbox']=np.array(bbox)
            dat_dict['moments']=np.array(moments)
            dat_dict['pixels']=np.array(images)
            dat_dict['row']=np.array(pos_drop)[:,0]
            dat_dict['col']=np.array(pos_drop)[:,1]
            
        if self._flagMasked:
            maxImg = filters.maximum_filter(imgDrop,footprint=self.footprint)
            maskMax = measurements.sum(self._mask,maxImg, drop_ind)
            imgDropMin = imgDrop.copy()
            imgDropMin[imgDrop==0]=(imgDrop.max()+1)
            minImg = filters.minimum_filter(imgDropMin,footprint=self.footprint)
            minImg[minImg==(imgDrop.max()+1)]=0
            maskMin = measurements.sum(self._mask,maxImg, drop_ind)
            maskDrop = maskMax+maskMin
            dat_dict['masked']=maskDrop

        self.dat = dat_dict                                             

        return ret_dict
