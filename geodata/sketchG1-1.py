
from dask.distributed import Client

import numpy as np

import matplotlib as mpl
import matplotlib.mlab as mlab
import matplotlib.pyplot as plt
import matplotlib.tri as tri
import cartopy.crs as ccrs

import geodata as gd
import pystare as ps
import h5py as h5
from pyhdf.SD import SD, SDC

import yaml

from sortedcontainers import SortedDict, SortedList

from stopwatch import sw_timer

###########################################################################
#

def npi64(i):
    return np.array(i,dtype=np.int64)

def npf64(i):
    return np.array(i,dtype=np.double)

class sid_geometry(object):
    def __init__(self,sids=None):
        self.triangles = SortedDict()
        self.tri_triang    = None
        if sids is not None:
            self.add(sids)
        return

    def add(self,sids):
        for sid in sids:
            if sid not in self.triangles.keys():
                self.tri_triang = None
                self.triangles[sid] = ps.triangulate_indices(np.array([sid],dtype=np.int64)) ## LLI
        return

    def triang(self):
        if self.tri_triang is None:
            k=0
            n = len(self.triangles.keys())
            lats = np.zeros([3*n],dtype=np.double)
            lons = np.zeros([3*n],dtype=np.double)
            intmat = []
            for sid in self.triangles:
                lats[k:k+3]   = self.triangles[sid][0][:]
                lons[k:k+3]   = self.triangles[sid][1][:]
                intmat.append([i+k for i in self.triangles[sid][2][0]])
                k=k+3
            self.tri_triang = tri.Triangulation(lats,lons,intmat)
        return self.tri_triang

    def get_sids_np(self):
        return np.array(self.triangles.keys(),dtype=np.int64)

class data_entry(object):
    def __init__(self,sid,datum):
        self.sid = sid
        self.datum = datum
        return

    def as_tuple(self):
        return (self.sid,self.datum)

class catalog_entry(object):
    def __init__(self,sid):
        self.data = {}
        self.sid  = sid
        self.geometry = sid_geometry([sid])
        return

    def add(self,key,datum):
        if key not in self.data.keys():
            self.data[key] = []
        self.data[key].append(datum) # datum is a data_entry
        return

class catalog(object):
    def __init__(self,resolution=None,sids=None):
        self.resolution = resolution
        self.result_size_limit = 4096
        self.sdict      = SortedDict()
        if sids is not None:
            for s in sids:
                self.open_entry(s)
        return

    def add(self,key,sid,datum,resolution=None):
        if resolution is not None:
            sidtest = gd.spatial_clear_to_resolution(gd.spatial_coerce_resolution(sid,resolution))
        elif self.resolution is not None:
            sidtest = gd.spatial_clear_to_resolution(gd.spatial_coerce_resolution(sid,self.resolution))
        else:
            sidtest = sid
        if sidtest not in self.sdict.keys():
            self.sdict[sidtest] = catalog_entry(sidtest) # construct with relevant resolution
        self.sdict[sidtest].add(key,data_entry(sid,datum))
        return

    def open_entry(self,sid):
        "Open a catalog entry, if it's not there. Expand sid, if needed."
        sidl=[sid]
        if self.resolution is not None:
            sidl = ps.expand_intervals(sidl,self.resolution,self.result_size_limit)
        for s in sidl:
            if s not in self.sdict.keys():
                self.sdict[s] = catalog_entry(s) # construct with appropriate resolution
        return

    def add_to_entry(self,key,sid,datum):
        "Add data to entry if it's there."
        if self.resolution is not None:
            sid_test = gd.spatial_clear_to_resolution(gd.spatial_coerce_resolution(sid,self.resolution))
        else:
            sid_test = sid
        # print('testing ',hex(sid_test), hex(sid))
        entry_open = sid_test in self.sdict.keys()
        if entry_open:
            # print(key,' adding ',data,' to ',hex(sid))
            self.add(key,sid,datum)
        return entry_open

    def get_all_data(self,key,interpolate=False):
        ret = []
        for s in self.sdict.keys():
            try:
                if len(self.sdict[s].data[key]) > 0:
                    ret = ret + self.sdict[s].data[key]
            except KeyError:
                continue
        return ret

    def get_data(self,key,sid):
        return self.sdict[sid].data[key]

###########################################################################
# https://stackoverflow.com/questions/41596386/tripcolor-using-rgb-values-for-each-vertex
#
def colors_to_cmap(colors):
    '''
    colors_to_cmap(nx3_or_nx4_rgba_array) yields a matplotlib colormap object that, when
    that will reproduce the colors in the given array when passed a list of n evenly
    spaced numbers between 0 and 1 (inclusive), where n is the length of the argument.

    Example:
      cmap = colors_to_cmap(colors)
      zs = np.asarray(range(len(colors)), dtype=np.float) / (len(colors)-1)
      # cmap(zs) should reproduce colors; cmap[zs[i]] == colors[i]
    '''
    colors = np.asarray(colors)
    if colors.shape[1] == 3:
        colors = np.hstack((colors, np.ones((len(colors),1))))
    steps = (0.5 + np.asarray(range(len(colors)-1), dtype=np.float))/(len(colors) - 1)
    return mpl.colors.LinearSegmentedColormap(
        'auto_cmap',
        {clrname: ([(0, col[0], col[0])] + 
                   [(step, c0, c1) for (step,c0,c1) in zip(steps, col[:-1], col[1:])] + 
                   [(1, col[-1], col[-1])])
         for (clridx,clrname) in enumerate(['red', 'green', 'blue', 'alpha'])
         for col in [colors[:,clridx]]},
        N=len(colors))

###########################################################################
# Helper functions

def with_hdf_get(h,var):
    sds = hdf.select(var)
    ret = sds.get()
    sds.endaccess()
    return ret

def slam(client,action,data,partition_factor=1.5):
    np = sum(client.nthreads().values())
    print('slam: np = %i'%np)
    shard_bounds = [int(i*len(data)/(1.0*partition_factor*np)) for i in range(int(partition_factor*np))] 
    if shard_bounds[-1] != len(data):
        shard_bounds = shard_bounds + [-1]
    data_shards = [data[shard_bounds[i]:shard_bounds[i+1]] for i in range(len(shard_bounds)-1)]
    print('ds len:        ',len(data_shards))
    print('ds item len:   ',len(data_shards[0]))
    print('ds type:       ',type(data_shards[0]))
    print('ds dtype:      ',data_shards[0].dtype)
    big_future = client.scatter(data_shards)
    results    = client.map(action,big_future)
    return results
    

def main():
    ###########################################################################
    # Data source
    dataPath="/home/mrilee/data/"
    
    ###########################################################################
    # MODIS

    modis_base   = "MOD05_L2."
    
    # modis_item   = "A2005349.2120.061.2017294065852"
    # modis_time_start = "2005-12-15T21:20:00"
    
    modis_item       = "A2005349.2125.061.2017294065400"
    modis_time_start = "2005-12-15T21:25:00"
    
    modis_suffix = ".hdf"
    modis_filename = modis_base+modis_item+modis_suffix

    # hdf        = SD(dataPath+modis_filename,SDC.READ)
    # ds_wv_nir  = hdf.select('Water_Vapor_Near_Infrared')
    
    fmt_suffix = ".h5"
    workFileName = "sketchG."+modis_base+modis_item+fmt_suffix
    print('loading ',workFileName)
    workFile = h5.File(workFileName,'r')
    sids = workFile['/image']['stare_spatial']
    lat  = workFile['/image']['Latitude']
    lon  = workFile['/image']['Longitude']
    data = workFile['/image']['Water_Vapor_Near_Infrared']
    workFile.close()

    modis_min = np.amin(data)
    modis_max = np.amax(data)
    sids = sids-1

    ###########################################################################
    # GOES
    
    goes_file='sketch9.2005.349.213015.h5'
    workFileName = goes_file
    workFile = h5.File(workFileName,'r')
    goes_sids = workFile['/image']['stare_spatial']
    goes_data = workFile['/image']['goes_b3']
    workFile.close()
    print('goes mnmx: ',np.amin(goes_data),np.amax(goes_data))
    goes_min = np.amin(goes_data)
    goes_max = np.amax(goes_data)
    goes_sids = goes_sids-1


    ###########################################################################
    # Plotting

    nrows = 2
    ncols = 3

    nrows = 1
    ncols = 1

    proj   = ccrs.PlateCarree()
    # proj   = ccrs.Mollweide()
    # proj   = ccrs.Mollweide(central_longitude=-160.0)
    transf = ccrs.Geodetic()

# https://stackoverflow.com/questions/33942233/how-do-i-change-matplotlibs-subplot-projection-of-an-existing-axis
    # plt.figure()
    fig,axs = plt.subplots(nrows=nrows,ncols=ncols,subplot_kw={'projection': proj})

    # axs.set_facecolor('k')
    # axs.patch.set_facecolor('black')
    # axs.set_facecolor('black')

    if nrows*ncols == 1:
        fig = [fig]
        axs = [axs]

    goes_line          = [False,False,False]
    modis_line         = [False,False,False]
    cover_plot         = [True, True, True ]
    goes_plot_1        = [True, False,True ]
    goes_plot_1_points = [True, False,True ]
    modis_plot_1       = [False,True, True ]
    plt_show_1         = [False,False,True ]

    goes_line           = [False,False,False  ,True  ,False ,True   ]
    modis_line          = [False,False,False  ,False ,True  ,True   ]
    cover_plot          = [False,False,False  ,False ,False ,False  ]
    goes_plot_1         = [True, False,True   ,True  ,False ,True   ]
    goes_plot_1_points  = [False,False,False  ,True  ,False ,True   ]
    modis_plot_1        = [False,True, True   ,False ,True  ,True   ]
    modis_plot_1_points = [False,False,False  ,False ,True  ,True   ] 
    plt_show_1          = [False,False,True   ,False ,False ,True   ]
    
    irow = [0,0,0,1,1,1]
    icol = [0,1,2,0,1,2]

    coastline_color = 'black'
    coastline_color = 'black'

    # blend
    blend_tripcolor_1       = True
    blend_tripcolor_1_res   = 10
    # blend_tripcolor_1_res   = 9 # FFE
    # blend_tripcolor_1_res   = 6 # Test
    blend_tripcolor_1_cmap  = None
    blend_tripcolor_1_alpha = 1
    blend_tripcolor_1_gamma_g  = 0.65
    blend_tripcolor_1_gamma_m  = 0.65
    if blend_tripcolor_1:
        goes_plot_1  = [False]*6
        modis_plot_1 = [False]*6
        # coastline_color = 'white'
        coastline_color = 'black'

    # 2020-0125 pix 1
    # goes_plot_1_res  = 9
    # modis_plot_1_res = 9
    #
    # goes_plot_1_res  = 6
    # modis_plot_1_res = 6
    #
    # plot_1_res = 9 # FFE
    plot_1_res = 6
    goes_plot_1_res  = plot_1_res
    modis_plot_1_res = plot_1_res

    # Colors
    goes_plot_1_tripcolor  = 'Reds'
    modis_plot_1_tripcolor = 'Blues'
    #
    common_alpha = 0.7
    goes_plot_1_alpha  = common_alpha
    modis_plot_1_alpha = common_alpha

    # recalculate=[True,False,False,True,False,False]
    recalculate=[True,False,True,True,False,True]
    cover_rads =[2.0,0,2, 0.125,0,0.125]
    # cover_rads =[2.0,0,0, 0.125,0,0]

    circle_plot =[ False   ,False       ,False   ,False         ,False   ,False ]
    circle_color=[ 'White' ,'lightgrey' ,'White' ,'navajowhite' ,'khaki' ,'White' ]
    modis_scatter_color=['darkcyan','darkcyan','darkcyan','darkcyan','cyan','cyan']

    nodes_cover=[1,2,1,1,2,1] # 1 == goes, 2 == modis, 0 == None
    # nodes_cover=[0,0,0,0,0,0]

    subplot_title = [
        "ROI+GOES"
        ,"ROI+MODIS"
        ,"ROI+GOES+MODIS"
        ,None
        ,None
        ,None
    ]
    
    # for iter in range(6):
    # for iter in [2,5]:
    if True:
        iter = 2

        ###########################################################################
        if recalculate[iter]:
            print('recalculating iter = ',iter)

            ###########################################################################
            cover_resolution = 11
            # cover_resolution = 12
            cover_type = 'circular'
            # cover_resolution = 6
            #+ cover_resolution = 5
            #+ cover_type = 'bounding_box'

            if cover_type == 'circular':
                ###########################################################################
                # HI 28.5N 177W
    
                # Near the Big Island
                cover_lat =   19.5-0.375
                cover_lon = -155.5+0.375
    
                # Midway Island
                # cover_lat =   28.2
                # cover_lon = -177.35
    
                # Ni'ihau
                # cover_lat =   21.9
                # cover_lon = -160.17
    
                cover_rad = cover_rads[iter]
                
                cover = ps.to_circular_cover(
                    cover_lat
                    ,cover_lon
                    ,cover_rad
                    ,cover_resolution)
                #    ,range_size_limit=2000)
            
            elif cover_type == 'bounding_box':
                # Set cover to "bounding box."
                cover_lat = np.array([15,15,38,38],dtype=np.float)
                cover_lon = np.array([-174,-145,-145,-174],dtype=np.float)
                cover = ps.to_hull_range_from_latlon(
                    cover_lat
                    ,cover_lon
                    ,cover_resolution
                )

            cover_cat = catalog(resolution=cover_resolution,sids=cover)
            cover_sids_min = np.amin(cover)
            cover_sids_max = np.amax(cover) # Need to convert to terminator
            cover_sids_max = gd.spatial_terminator(cover_sids_max)
        
            # for k in list(cover_cat.sdict.keys()):
            #     print('cc: ',hex(k))

            ###########################################################################
            #
            gm_cat_resolution = 5
            gm_catalog = catalog(resolution=gm_cat_resolution)
            k=0
            for i in range(10):
                while(goes_sids[k]<0):
                    k=k+1
                # print('adding: ','0x%016x'%goes_sids[k],k)
                gm_catalog.add('goes',goes_sids[k],goes_data[k])
                k=k+1
        
            for i in range(10):
                # print('adding: ','0x%016x'%sids[i])
                gm_catalog.add('modis',sids[i],data[i])
        
            k = 0
            # for i in range(10):
            idx = np.arange(goes_sids.size)[np.where( (goes_sids > cover_sids_min) & (goes_sids < cover_sids_max))]
            for k in range(len(idx)):
                # while(goes_sids[k]<0):
                #    k=k+1
                if goes_sids[idx[k]] > 0:
                    cover_cat.add_to_entry('goes',goes_sids[idx[k]],goes_data[idx[k]])
                # k=k+1
        
            idx = np.arange(sids.size)[np.where( (sids > cover_sids_min) & (sids < cover_sids_max))]
            for k in range(len(idx)):
                if sids[idx[k]] > 0:
                    cover_cat.add_to_entry('modis',sids[idx[k]],data[idx[k]])
        
        
            # print(yaml.dump(gm_catalog))
            # exit()
            #
            ###########################################################################

        print('plotting iter ',iter)
        
        if nrows*ncols == 1:
            ax = axs[0]
        else:
            ax = axs[irow[iter],icol[iter]]
        
        if subplot_title[iter] is not None:
            ax.set_title(subplot_title[iter])
        if False:
            ax.set_global()
        if True:
            ax.coastlines(color=coastline_color)
    

        if iter == 0:
            x0 = 0.05
            y0 = 0.025; dy = 0.025
            plt.figtext(x0,y0+0*dy
                        ,"MODIS: "+"sketchG."+modis_base+modis_item+fmt_suffix+', Water_Vapor_Near_Infrared, resolution = %i'%(sids[10000]&31)
                        ,fontsize=10)
            k=0;
            while goes_sids[k]<0:
                k=k+1
            plt.figtext(x0,y0+1*dy
                        ,"GOES:  "+goes_file+' BAND_3 (6.7mu), resolution = %i'%(goes_sids[k]&31)
                        ,fontsize=10)

            if cover_type == 'circular':
                plt.figtext(x0,y0+2*dy
                            ,"ROI Cover: resolution = %d, radius = %0.2f (upper) %0.3f (lower) degrees, center = 0x%016x"%(cover_resolution,cover_rads[0],cover_rads[3],ps.from_latlon(npf64([cover_lat]),npf64([cover_lon]),cover_resolution)[0])
                            ,fontsize=10)

            # plt.show()
            # exit()

        if False:
            lli = ps.triangulate_indices(cover)
            ax.triplot(tri.Triangulation(lli[0],lli[1],lli[2])
                        ,'g-',transform=transf,lw=1,markersize=3)
    
        if True:
            if goes_plot_1[iter]:
                cc_data = cover_cat.get_all_data('goes')
                csids,sdat = zip(*[cd.as_tuple() for cd in cc_data])
                if goes_plot_1_points[iter]:
                    glat,glon = ps.to_latlon(csids)

                # csids_at_res = list(map(gd.spatial_clear_to_resolution,csids))
                # cc_data_accum = dict()
                # for cs in csids_at_res:
                #     cc_data_accum[cs] = []
                # for ics in range(len(csids_at_res)):
                #     cc_data_accum[csids_at_res[ics]].append(sdat[ics])
                # for cs in cc_data_accum.keys():
                #     if len(cc_data_accum[cs]) > 1:
                #         cc_data_accum[cs] = [sum(cc_data_accum[cs])/(1.0*len(cc_data_accum[cs]))]
                # tmp_values = np.array(list(cc_data_accum.values()))
                # vmin = np.amin(tmp_values)
                # vmax = np.amax(np.array(tmp_values))

                cc_data_accum,vmin,vmax = gd.simple_collect(csids,sdat,force_resolution=goes_plot_1_res)

                # print('a100: ',cc_data)
                # print('cc_data       type: ',type(cc_data))
                # print('cc_data[0]    type: ',type(cc_data[0]))
                
                for cs in cc_data_accum.keys():
                    # print('item: ',hex(cs),cc_data_accum[cs])
                    lli    = ps.triangulate_indices([cs])
                    triang = tri.Triangulation(lli[0],lli[1],lli[2])
                    cd_plt = np.array(cc_data_accum[cs])
                    # print('cd_plt type ',type(cd_plt))
                    # print('cd_plt shape ',cd_plt.shape)
                    # print('cd_plt type ',type(cd_plt[0]))
                    if goes_line[iter]:
                        ax.triplot(triang,'r-',transform=transf,lw=1.5,markersize=3,alpha=0.5)
                    # ax.tripcolor(triang,facecolors=cd_plt,vmin=goes_min,vmax=goes_max,cmap='Reds',alpha=0.4)
                    ax.tripcolor(triang
                                 ,facecolors=cd_plt
                                 ,edgecolors='k',lw=0
                                 ,shading='flat'
                                 ,vmin=vmin,vmax=vmax,cmap=goes_plot_1_tripcolor,alpha=goes_plot_1_alpha)
    
                # for cd in cc_data:
                #     lli    = ps.triangulate_indices([cd.sid])
                #     triang = tri.Triangulation(lli[0],lli[1],lli[2])
                #     cd_plt = np.array([cd.datum])
                #     if goes_line[iter]:
                #         ax.triplot(triang,'r-',transform=transf,lw=3,markersize=3,alpha=0.5)
                #     ax.tripcolor(triang,facecolors=cd_plt,vmin=goes_min,vmax=goes_max,cmap='Reds',alpha=0.4)
    
            if modis_plot_1[iter]:
                cc_data_m = cover_cat.get_all_data('modis')
                csids,sdat = zip(*[cd.as_tuple() for cd in cc_data_m])
                # mlat,mlon = ps.to_latlon(csids)

                cc_data_m_accum,vmin,vmax = gd.simple_collect(csids,sdat,force_resolution=modis_plot_1_res)

                for cs in cc_data_m_accum.keys():
                    lli    = ps.triangulate_indices([cs])
                    triang = tri.Triangulation(lli[0],lli[1],lli[2])
                    cd_plt = np.array(cc_data_m_accum[cs])
                    # print('lli[0] len ',len(lli[0]))
                    # print('cd_plt len ', len(cd_plt))
                    # print('cd_plt type ',type(cd_plt))
                    # print('cd_plt shape ',cd_plt.shape)
                    # print('cd_plt type ',type(cd_plt[0]))
                    if modis_line[iter]:
                        ax.triplot(triang,'b-',transform=transf,lw=1.5,markersize=3,alpha=0.5)
                    # ax.tripcolor(triang,facecolors=cd_plt,vmin=goes_min,vmax=goes_max,cmap='Blues',alpha=0.4)
                    ax.tripcolor(triang
                                 ,facecolors=cd_plt
                                 ,edgecolors='k',lw=0
                                 ,shading='flat'
                                 ,vmin=vmin,vmax=vmax,cmap=modis_plot_1_tripcolor,alpha=modis_plot_1_alpha)

                # for cd in cc_data_m:
                #     lli    = ps.triangulate_indices([cd.sid])
                #     triang = tri.Triangulation(lli[0],lli[1],lli[2])
                #     cd_plt = np.array([cd.datum])
                #     if modis_line[iter]:
                #         ax.triplot(triang,'b-',transform=transf,lw=1,markersize=3,alpha=0.5)
                #     ax.tripcolor(triang,facecolors=cd_plt,vmin=modis_min,vmax=modis_max,cmap='Blues',alpha=0.4)
                if modis_plot_1_points[iter]:
                    mlat,mlon = ps.to_latlon(csids)
                    ax.scatter(mlon,mlat,s=8,c=modis_scatter_color[iter])
                    # ax.scatter(mlon,mlat,s=8,c='cyan')
                    # ax.scatter(mlon,mlat,s=8,c='darkcyan')

            # blend_tripcolor_1 = False
            # blend_tripcolor_res_1  = 6
            # blend_tripcolor_1_cmap  = None
            # blend_tripcolor_1_alpha = 1
            if blend_tripcolor_1:
                cc_data = cover_cat.get_all_data('goes')
                csids,sdat = zip(*[cd.as_tuple() for cd in cc_data])
                cc_data_accum,vmin,vmax = gd.simple_collect(csids,sdat,force_resolution=blend_tripcolor_1_res)

                cc_data_m = cover_cat.get_all_data('modis')
                csids_m,sdat_m = zip(*[cd.as_tuple() for cd in cc_data_m])
                cc_data_m_accum,vmin_m,vmax_m = gd.simple_collect(csids_m,sdat_m,force_resolution=blend_tripcolor_1_res)

                data_accum_keys = set()
                for cs in cc_data_accum.keys():
                    data_accum_keys.add(cs)
                for cs in cc_data_m_accum.keys():
                    data_accum_keys.add(cs)
                for cs in data_accum_keys:
                    # print('item: ',hex(cs),cc_data_accum[cs])
                    lli    = ps.triangulate_indices([cs])
                    triang = tri.Triangulation(lli[0],lli[1],lli[2])
                    try:
                        cd_plt_g = (np.array(cc_data_accum[cs])-vmin)/(vmax-vmin)
                        cd_plt_g   = cd_plt_g ** blend_tripcolor_1_gamma_g
                    except:
                        cd_plt_g = np.array([0])
                    try:
                        cd_plt_m = (np.array(cc_data_m_accum[cs])-vmin_m)/(vmax_m-vmin_m)
                        cd_plt_m = cd_plt_m ** blend_tripcolor_1_gamma_m
                    except:
                        cd_plt_m = np.array([0])
                    ######
                    # blend 1 & 2
                    # cd_plt = np.array([[cd_plt_g,0,cd_plt_m]])
                    ######
                    # blend 3
                    # print('len: ',cd_plt_g.shape,cd_plt_m.shape)
                    cd_plt = np.array([[cd_plt_g[0],0.5*(cd_plt_g+cd_plt_m)[0],cd_plt_m[0]]])
                    cd_cmp = colors_to_cmap(cd_plt)
                    zs = np.asarray(range(3),dtype=np.float)/2.0

                    ax.tripcolor(triang
                                 ,zs
                                 ,cmap=cd_cmp
                                 # ,facecolors=cd_plt
                                 ,edgecolors='k',lw=0
                                 ,shading='gouraud'
                                 # ,shading='flat'
                                 # ,vmin=vmin,vmax=vmax
                                 # ,cmap=blend_tripcolor_1_cmap
                                 ,alpha=blend_tripcolor_1_alpha)
                                 # ,vmin=vmin,vmax=vmax,cmap=blend_tripcolor_1_cmap,alpha=blend_tripcolor_1_alpha)
            
            if goes_plot_1[iter]:
                if goes_plot_1_points[iter]:
                    ax.scatter(glon,glat,s=8,c='black')

            if nodes_cover[iter] > 0:
                if nodes_cover[iter] == 1:
                    cc_data_ = cover_cat.get_all_data('goes')
                else:
                    cc_data_ = cover_cat.get_all_data('modis')
                sids_,dat_ = zip(*[cd.as_tuple() for cd in cc_data_])
                # print('sids_ len: ',len(sids_))
                sids_test   = gd.spatial_clear_to_resolution(npi64([gd.spatial_coerce_resolution(s,gm_cat_resolution) for s in sids_]))
                # print('sids_tlen: ',len(sids_test))
                if cover_type == 'circular':
                    print('cover: 0x%016x'%ps.from_latlon(npf64([cover_lat]),npf64([cover_lon]),cover_resolution)[0])
                geom_test   = sid_geometry(sids_test)
                for s in geom_test.triangles.keys():
                    print(iter,' 0x%016x'%s)
                triang_test = geom_test.triang()
                # ax.triplot(triang_test,'g-',transform=transf,lw=1.0,markersize=3,alpha=0.75)
                ax.triplot(triang_test,'k-',transform=transf,lw=1.0,markersize=3,alpha=0.5)
    
            if False:
                for i in range(0,10):
                    k = cover_cat.sdict.peekitem(i)[0]
                    triang = cover_cat.sdict[k].geometry.triang()
                    ax.triplot(triang,'b-',transform=transf,lw=1,markersize=3,alpha=0.5)

            if cover_plot[iter]:
                # lli = ps.triangulate_indices(ps.expand_intervals(cover,9,result_size_limit=2048))
                lli = ps.triangulate_indices(cover)
                ax.triplot(tri.Triangulation(lli[0],lli[1],lli[2])
                           ,'k-',transform=transf,lw=1,markersize=3,alpha=0.5)
                           # ,'g-',transform=transf,lw=1,markersize=3)

            if False:
                # k = gm_catalog.sdict.keys()[0]
                # for k in gm_catalog.sdict.keys():
                for i in range(0,3):
                    k = gm_catalog.sdict.peekitem(i)[0]
                    triang = gm_catalog.sdict[k].geometry.triang()
                    ax.triplot(triang,'r-',transform=transf,lw=1,markersize=3)

            if circle_plot[iter]:
                phi=np.linspace(0,2*np.pi,64)
                # rad=cover_rad
                rad=0.125
                ax.plot(cover_lon+rad*np.cos(phi),cover_lat+rad*np.sin(phi),transform=transf,color=circle_color[iter])

            # ax.set_facecolor('k')

            if plt_show_1[iter]:
                plt.show()
                
###########################################################################
#
#    if False:
#        sw_timer.stamp('triangulating')
#        print('triangulating')
#        client = Client()
#        for lli_ in slam(client,ps.triangulate_indices,sids):
#            sw_timer.stamp('slam iteration')
#            print('lli_ type: ',type(lli_))
#            lli = lli_.result()
#            sw_timer.stamp('slam result')
#            print('lli type:  ',type(lli))
#            triang = tri.Triangulation(lli[0],lli[1],lli[2])
#            sw_timer.stamp('slam triang')
#            plt.triplot(triang,'r-',transform=transf,lw=1.5,markersize=3,alpha=0.5)
#            sw_timer.stamp('slam triplot')
#
#    sw_timer.stamp('plt show')
#    # lons,lats,intmat=ps.triangulate_indices(sids)
#    # triang = tri.Triangulation(lons,lats,intmat)
#    # plt.triplot(triang,'r-',transform=transf,lw=1.5,markersize=3)
#
#    plt.show()

#    client.close()

    print(sw_timer.report_all())

if __name__ == "__main__":

    main()

