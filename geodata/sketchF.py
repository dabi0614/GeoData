#!/usr/bin/env python3

import h5py as h5
from netCDF4 import Dataset
import numpy as np
import pystare as ps
import json
from sortedcontainers import SortedDict, SortedList

import geodata as gd
# import os,fnmatch
import yaml

def hex16(i):
    return "0x%016x"%i

with open("config.yaml") as f:
    config = yaml.load(f,Loader=yaml.FullLoader)
    # print(config)
    # print('')
    # if config['run']['action'] == "list":
    #     print("%s %s"%("list",config['data_source']['directory']))

goes_b5_catalog = gd.data_catalog(config['data_sources']['goes_gvar_img_b5'])
goes_files = goes_b5_catalog.get_files()

m2_catalog = gd.data_catalog(config['data_sources']['merra2'])
m2_files = m2_catalog.get_files()
m2_tid_index = m2_catalog.get_tid_centered_index()

for entry in goes_files:
    gtid = gd.temporal_id_centered_from_filename(entry)
    m2_match = m2_catalog.find(gtid)
    print('matched pair: 0x%016x % 40s % 40s'%(gtid,entry,m2_match))
    # print('matched pair: 0x%016x % 40s % 40s'%(gtid,entry,gd.temporal_match_to_merra2(gtid,m2_tid_index,dataPath=m2_catalog.config['directory'])[0]))

###########################################################################

class join_value(object):
    def __init__(self):
        self.bandmaps = {}
        return

    def contains(self,id):
        return id in self.bandmaps.keys()

    def add(self,bandname,id):
        if bandname not in self.bandmaps.keys():
            self.bandmaps[bandname] = SortedList()
        self.bandmaps[bandname].add(id)
        return

    def get(self,bandname):
        return self.bandmaps[bandname]

    def toJSON(self):
        str = ""
        output = {}
        for imap in self.bandmaps.keys():
            # print('saving ',imap)
            value = [int(i) for i in self.bandmaps[imap]]
            output[imap]=value
            # print('value:  ',value)
            # str = str + json.dumps( {imap:[i for i in self.bandmaps[imap]]} )
            # str = str + json.dumps( {imap:value} ) + '\n'
        str = json.dumps(output)+"\n"
        return str

def join_goes_and_m2_to_h5(goes_datapath,goes_filenames,m2_datapath,m2_file_name,workFileName):

    ##### HDF5 Data types for output
    image_dtype = np.dtype([
        ('stare_spatial',np.int64)
        ,('stare_temporal',np.int64)
        ,('goes_src_coord',np.int64)
        ,('goes_b3',np.int64)
        ,('goes_b4',np.int64)
        ,('goes_b5',np.int64)
        ,('merra2_src_coord',np.int64)
        ,('merra2_tpw',np.int64)
    ])
    image_description_dtype = np.dtype([
        ('nx',np.int)
        ,('ny',np.int)
    ])
    m2_description_dtype = np.dtype([
        ('nx',np.int)
        ,('ny',np.int)
        ,('tpw_offset',np.double)
        ,('tpw_scale',np.double)
    ])

    goes_bandnames = {
        "BAND_03":"goes_b3"
        ,"BAND_04":"goes_b4"
        ,"BAND_05":"goes_b5"
    }
    goes_filenames_valid = []
    for i in goes_filenames:
        goes_band     = i.split('.')[4]    
        if goes_band in goes_bandnames.keys():
            goes_filenames_valid.append(i)
    if len(goes_filenames_valid) == 0:
        print("*ERROR* Join and save -- no valid GOES filenames. Returning.")
        return

    # print('goes_filenames:       ',goes_filenames)
    # print('goes_filenames_valid: ',goes_filenames_valid)

    ###########################################################################
    ##### MERRA-2

    m2_dLat    = 0.5
    m2_dLon    = 5.0/8.0
    m2_dLatkm  = m2_dLat * gd.re_km/gd.deg_per_rad
    m2_dLonkm  = m2_dLon * gd.re_km/gd.deg_per_rad
    m2_ds  = Dataset(m2_datapath+m2_file_name)
    m2_lat,m2_lon = np.meshgrid(m2_ds['lat'],m2_ds['lon'])
    m2_lat     = m2_lat.flatten()
    m2_lon     = m2_lon.flatten()
    m2_idx_ij  = np.arange(m2_lat.size,dtype=np.int64)
    m2_resolution = int(gd.resolution(m2_dLonkm*2))
    m2_indices = ps.from_latlon(m2_lat,m2_lon,m2_resolution)
    m2_term = gd.spatial_terminator(m2_indices)
    m2_tid     = gd.merra2_stare_time(m2_ds)

    ###########################################################################
    ##### GOES

    igoes = 0
    goes_band = goes_filenames_valid[igoes].split('.')[4]
    goes_bandname = goes_bandnames[goes_band]

    goes_ds  = Dataset(goes_datapath+goes_filenames_valid[igoes])
    goes_tid = gd.goes10_img_stare_time(goes_ds)

    ##### MERRA-2 at the GOES time
    fine_match = ps.cmp_temporal(np.array(goes_tid,dtype=np.int64),m2_tid)
    m2_ifm     = np.nonzero(fine_match)[0]
    m2_dataDayI     = m2_ds['TQI'][m2_ifm,:,:]
    m2_dataDayL     = m2_ds['TQL'][m2_ifm,:,:]
    m2_dataDayV     = m2_ds['TQV'][m2_ifm,:,:]
    m2_dataDay      = m2_dataDayI + m2_dataDayL + m2_dataDayV
    if m2_ifm.size > 1:
        print('multiple hits on 2m')
        m2_dataDay  = np.mean(m2_dataDay,axis=1)
        print('m2_dataDay shape: ',n2_dataDay.shape)
    m2_data         = m2_dataDay[:,:].T
    m2_data_flat    = m2_data.flatten()

    g_lat = goes_ds['lat'][:,:].flatten()
    g_lon = goes_ds['lon'][:,:].flatten()
    g_idx_valid = np.where((g_lat>=-90.0) & (g_lat<=90.0))
    g_idx_invalid = np.where(((g_lat<-90.0) | (g_lat>90.0)))
    goes_indices = np.full(g_lat.shape,-1,dtype=np.int64)
    goes_indices[g_idx_valid] = ps.from_latlon(g_lat[g_idx_valid],g_lon[g_idx_valid],int(gd.resolution(goes_ds['elemRes'][0])))

    ##### Allocate MERRA-2 arrays co-aligned with GOES
    m2_src_coord_h5 = np.full(g_lat.shape,-1,dtype=np.int64)
    m2_tpw_h5       = np.full(g_lat.shape,-1,dtype=np.int64)

    #####

    join_resolution = m2_resolution
    join = SortedDict()
    
    ktr=0
    for k in range(len(g_idx_valid[0])):
        id = g_idx_valid[0][k]
        jk = gd.spatial_clear_to_resolution(gd.spatial_coerce_resolution(goes_indices[id],join_resolution))
        if jk not in join.keys():
            join[jk] = join_value()
        join[jk].add(goes_bandname,id)
        ktr = ktr + 1; 
        # if ktr > 10:
        #     break
        #     # exit();
    
    for k in range(len(m2_indices)):
        jk = gd.spatial_clear_to_resolution(m2_indices[k])
        if jk not in join.keys():
            join[jk] = join_value()
        join[jk].add('m2',k)

    ###########################################################################
    
    tpw_scale  = 0.001;
    tpw_offset = 0;

    ###########################################################################
    ##### JOIN

    jkeys=join.keys()
    ktr = 0; nktr = len(jkeys) # gd_idx_valid is a tuple with one element
    dktr = nktr/10.0
    elements_pushed = 0
    print('Push joined m2 data into the dataset n = ',nktr)
    for k in range(nktr):
        ktr = ktr + 1
        if (ktr % int(dktr)) == 0:
            print(int((10.0*ktr)/dktr),'% complete, ',elements_pushed,' elements pushed.')
        sid = jkeys[k]
        if join[sid].contains(goes_bandname):
            if join[sid].contains('m2'):
                m2s = join[sid].get('m2')[0] # Grab the first one
                m2_src_coord_h5[join[sid].get(goes_bandname)] = m2s
                # m2_tpw_h5[join[sid].get(goes_bandname)]       = (m2_data_flat[m2s]-tpw_offset)/tpw_scale
                avg = (np.mean(m2_data_flat[join[sid].get('m2')])-tpw_offset)/tpw_scale
                m2_tpw_h5[join[sid].get(goes_bandname)]       = avg
                elements_pushed = elements_pushed + len(join[sid].get(goes_bandname))


    ###########################################################################
    ##### HDF5 SAVE DATASET

    workFile = h5.File(workFileName,'w')
    image_ds = workFile.create_dataset('image',[goes_ds['data'].size],dtype=image_dtype)
    image_description_ds = workFile.create_dataset('image_description',[],dtype=image_description_dtype)
    m2_description_ds = workFile.create_dataset('merra2_description',[],dtype=m2_description_dtype)
    
    workFile['/image']['stare_spatial'] = goes_indices[:]
    workFile['/image']['stare_temporal'] = gd.goes10_img_stare_time(goes_ds)[0]
    workFile['/image']['goes_src_coord'] = np.arange(g_lat.size,dtype=np.int64)
    workFile['/image']['merra2_src_coord'] = m2_src_coord_h5.flatten()
    workFile['/image']['merra2_tpw']       = m2_tpw_h5.flatten()

    # oops
    # workFile['/image_description']['nx'] = goes_ds['data'].shape[1]
    # workFile['/image_description']['ny'] = goes_ds['data'].shape[2]
    workFile['/image_description']['nx'] = goes_ds['data'].shape[2]
    workFile['/image_description']['ny'] = goes_ds['data'].shape[1]
    print('image nx,ny: ',goes_ds['data'].shape[2],goes_ds['data'].shape[1])

    workFile['/merra2_description']['nx'] = 576
    workFile['/merra2_description']['ny'] = 361
    workFile['/merra2_description']['tpw_offset'] = tpw_offset
    workFile['/merra2_description']['tpw_scale']  = tpw_scale

    # workFile['/image']['goes_b3'] = goes_ds['data'][0,:,:].flatten()
    # workFile['/image']['goes_b4'] = goes_ds['data'][0,:,:].flatten()

    while igoes < len(goes_filenames_valid):
        print(i,' saving ',goes_bandname,' from file ',goes_filenames_valid[igoes])
        workFile['/image'][goes_bandname] = goes_ds['data'][0,:,:].flatten()
        goes_ds.close()
        igoes = igoes + 1
        # Assume remaining GOES bands have the same image sizes and locations.
        if igoes < len(goes_filenames_valid):
            goes_band     = goes_filenames_valid[igoes].split('.')[4]
            goes_ds       = Dataset(goes_datapath+goes_filenames_valid[igoes])
            goes_bandname = goes_bandnames[goes_band]

    workFile.close()

    return

### GOES DATASET
goes_b5_dataPath = "/home/mrilee/data/"
goes_b5_dataFile = "goes10.2005.349.003015.BAND_05.nc"
goes_b5_fqFilename = goes_b5_dataPath+goes_b5_dataFile

### MERRA 2 DATASET
dataPath   = "/home/mrilee/data/"
dataFile   = "MERRA2_300.tavg1_2d_slv_Nx.20051215.nc4"
fqFilename = dataPath+dataFile

join_goes_and_m2_to_h5(
    "/home/mrilee/data/"
    ,["goes10.2005.349.003015.BAND_03.nc","goes10.2005.349.003015.BAND_04.nc","goes10.2005.349.003015.BAND_05.nc"]
    ,"/home/mrilee/data/"
    ,"MERRA2_300.tavg1_2d_slv_Nx.20051215.nc4"
    ,"sketchF.h5")





