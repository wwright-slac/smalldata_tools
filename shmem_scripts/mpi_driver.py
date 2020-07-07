# Local imports
from mpi_worker import MpiWorker
from mpi_master import MpiMaster
from smalldata_tools.SmallDataUtils import defaultDetectors

# Import mpi, check that we have enough cores
from mpi4py import MPI
comm = MPI.COMM_WORLD
size = comm.Get_size()
rank = comm.Get_rank()
assert size > 1, 'At least 2 MPI ranks required'
import psana
import logging
f = '%(asctime)s - %(levelname)s - %(filename)s:%(funcName)s - %(message)s'
logging.basicConfig(level=logging.DEBUG, format=f)
logger = logging.getLogger(__name__)

# All the args
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('-exprun', help='psana experiment/run string (e.g. exp=xppd7114:run=43)', type=str, default='')
parser.add_argument('-dsname', help='data source name', type=str, default='')
parser.add_argument('-nevts', help='number of events', default=50, type=int)
parser.add_argument('-cfg_file', help='if specified, has information about what metadata to use', type=str)
args = parser.parse_args()

# Define data source name
dsname = args.dsname
if not dsname:
    if 'shmem' == args.exprun:
        dsname = 'shmem=psana.0:stop=no'
    elif 'shmem' in args.exprun:
        dsname = ''.join([args.exprun, ':smd'])
    else:
        raise ValueError('Data source name could not be determined')

if args.cfg_file:
    raise NotImplementedError('config file not implemented')

# Can look at ways of automating this later
#if not args.exprun:
#    raise ValueError('You have not provided an experiment')

hutch = 'xcs'#args.exprun.split('exp=')[1][:3]
dsname = 'shmem=psana.0:stop=no'
if rank == 0:
    master = MpiMaster(rank)
    master.start_run()
else:
    ds = psana.DataSource(dsname)
    detectors = defaultDetectors(hutch)
    worker = MpiWorker(ds, args.nevts, detectors, rank)
    worker.start_run()
