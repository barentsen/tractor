import matplotlib
matplotlib.use('Agg')
import numpy as np
import pylab as plt
import os
import logging
from glob import glob

from astrometry.util.fits import *
from astrometry.util.sdss_radec_to_rcf import *
from astrometry.util.file import *
from astrometry.util.plotutils import *
from astrometry.util.util import *
from astrometry.util.ttime import *
from astrometry.util.resample import *
from astrometry.sdss import *
from astrometry.libkd.spherematch import *

from tractor import *
from tractor.sdss import *
from tractor.sdss_galaxy import *
from tractor.emfit import em_fit_2d
from tractor.fitpsf import em_init_params

from photoobjs import *

data_dir = 'data/cs82'
window_flist = 'window_flist.fits'

def get_cs82_sources(T, maglim=25, bands=['u','g','r','i','z']):
    srcs = Catalog()
    isrcs = []
    for i,t in enumerate(T):
        if t.chi2_psf < t.chi2_model and t.mag_psf <= maglim:
            #print 'PSF'
            themag = t.mag_psf
            nm = NanoMaggies.magToNanomaggies(themag)
            m = NanoMaggies(order=bands, **dict([(k, nm) for k in bands]))
            srcs.append(PointSource(RaDecPos(t.ra, t.dec), m))
            isrcs.append(i)
            continue

        if t.mag_disk > maglim and t.mag_spheroid > maglim:
            #print 'Faint'
            continue

        # deV: spheroid
        # exp: disk

        dmag = t.mag_spheroid
        emag = t.mag_disk

        # SPHEROID_REFF [for Sersic index n= 1] = 1.68 * DISK_SCALE

        if dmag <= maglim:
            shape_dev = GalaxyShape(t.spheroid_reff_world * 3600.,
                                    t.spheroid_aspect_world,
                                    t.spheroid_theta_world + 90.)

        if emag <= maglim:
            shape_exp = GalaxyShape(t.disk_scale_world * 1.68 * 3600.,
                                    t.disk_aspect_world,
                                    t.disk_theta_world + 90.)

        pos = RaDecPos(t.alphamodel_j2000, t.deltamodel_j2000)

        isrcs.append(i)
        if emag > maglim and dmag <= maglim:
            nm = NanoMaggies.magToNanomaggies(dmag)
            m_dev = NanoMaggies(order=bands, **dict([(k, nm) for k in bands]))
            srcs.append(DevGalaxy(pos, m_dev, shape_dev))
            continue
        if emag <= maglim and dmag > maglim:
            nm = NanoMaggies.magToNanomaggies(emag)
            m_exp = NanoMaggies(order=bands, **dict([(k, nm) for k in bands]))
            srcs.append(ExpGalaxy(pos, m_exp, shape_exp))
            continue

        # print 'Composite'
        nmd = NanoMaggies.magToNanomaggies(dmag)
        nme = NanoMaggies.magToNanomaggies(emag)
        nm = nmd + nme
        fdev = (nmd / nm)
        m = NanoMaggies(order=bands, **dict([(k, nm) for k in bands]))
        srcs.append(FixedCompositeGalaxy(pos, m, fdev, shape_exp, shape_dev))

    #print 'Sources:', len(srcs)
    return srcs, np.array(isrcs)


def getTables(cs82field, enclosed=True, extra_cols=[]):
    fn = os.path.join(data_dir, 'masked.%s_y.V2.7A.swarp.cut.deVexp.fit' % cs82field)
    print 'Reading', fn
    T = fits_table(fn,
            hdu=2, column_map={'ALPHA_J2000':'ra',
                               'DELTA_J2000':'dec'},
            columns=[x.upper() for x in
                     ['ALPHA_J2000', 'DELTA_J2000',
                      'chi2_psf', 'chi2_model', 'mag_psf', 'mag_disk',
                      'mag_spheroid', 'disk_scale_world', 'disk_aspect_world',
                      'disk_theta_world', 'spheroid_reff_world',
                      'spheroid_aspect_world', 'spheroid_theta_world',
                      'alphamodel_j2000', 'deltamodel_j2000'] + extra_cols])
    ra0,ra1 = T.ra.min(), T.ra.max()
    dec0,dec1 = T.dec.min(), T.dec.max()
    print 'RA', ra0,ra1
    print 'Dec', dec0,dec1
    T.index = np.arange(len(T))

    # ASSUME no RA wrap-around in the CS82 catalog
    trad = 0.5 * np.hypot(ra1 - ra0, dec1 - dec0)
    tcen = radectoxyz((ra1+ra0)*0.5, (dec1+dec0)*0.5)

    frad = 0.5 * np.hypot(13., 9.) / 60.

    fn = 'sdssfield-%s.fits' % cs82field
    if os.path.exists(fn):
        print 'Reading', fn
        F = fits_table(fn)
    else:
        F = fits_table(window_flist)

        # These runs don't appear in DAS
        F.cut(F.rerun != "157")

        # For Stripe 82, mu-nu is aligned with RA,Dec.
        rd = []
        rd.append(munu_to_radec_deg(F.mu_start, F.nu_start, F.node, F.incl))
        rd.append(munu_to_radec_deg(F.mu_end,   F.nu_end,   F.node, F.incl))
        rd = np.array(rd)
        F.ra0  = np.min(rd[:,0,:], axis=0)
        F.ra1  = np.max(rd[:,0,:], axis=0)
        F.dec0 = np.min(rd[:,1,:], axis=0)
        F.dec1 = np.max(rd[:,1,:], axis=0)

        I = np.flatnonzero((F.ra0 <= T.ra.max()) *
                           (F.ra1 >= T.ra.min()) *
                           (F.dec0 <= T.dec.max()) *
                           (F.dec1 >= T.dec.min()))
        print 'Possibly overlapping fields:', len(I)
        F.cut(I)

        # When will I ever learn not to cut on RA boxes when there is wrap-around?
        xyz = radectoxyz(F.ra, F.dec)
        r2 = np.sum((xyz - tcen)**2, axis=1)
        I = np.flatnonzero(r2 < deg2distsq(trad + frad))
        print 'Possibly overlapping fields:', len(I)
        F.cut(I)

        F.enclosed = ((F.ra0 >= T.ra.min()) *
                      (F.ra1 <= T.ra.max()) *
                      (F.dec0 >= T.dec.min()) *
                      (F.dec1 <= T.dec.max()))
        
        # Sort by distance from the center of the field.
        ra  = (T.ra.min()  + T.ra.max() ) / 2.
        dec = (T.dec.min() + T.dec.max()) / 2.
        I = np.argsort( ((F.ra0  + F.ra1 )/2. - ra )**2 +
                        ((F.dec0 + F.dec1)/2. - dec)**2 )
        F.cut(I)

        F.writeto(fn)
        print 'Wrote', fn

    if enclosed:
        F.cut(F.enclosed)
        print 'Enclosed fields:', len(F)
        
    return T,F


def main(opt, cs82field, prefix):
    t0 = Time()
    
    bands = opt.bands

    if opt.plots:
        ps = PlotSequence(opt.prefix)
        plots = True
    else:
        ps = None
        plots = False
    
    version = get_svn_version()
    print 'SVN version info:', version
    
    T,F = getTables(cs82field, enclosed=False)

    sdss = DR9(basedir='data/unzip')
    if opt.local:
        sdss.useLocalTree()
        sdss.saveUnzippedFiles('data/unzip')

    ### HACK -- ignore 0/360 issues
    ra0 = T.ra.min()
    ra1 = T.ra.max()
    dec0 = T.dec.min()
    dec1 = T.dec.max()
    print 'RA range:', ra0, ra1
    print 'Dec range:', dec0, dec1
    # check for wrap-around
    assert(ra1 - ra0 < 2.)

    # Read SDSS objects to initialize fluxes (and fill in holes?)
    # create fake WCS for this area...
    pixscale = 1./3600.
    decpix = int(np.ceil((dec1 - dec0) / pixscale))
    # HACK -- ignoring cos(dec)
    rapix = int(np.ceil((ra1 - ra0) / pixscale))
    wcs = Tan((ra0 + ra1)/2., (dec0+dec1)/2., rapix/2 + 1, decpix/2 + 1,
              pixscale, 0., 0., pixscale, rapix, decpix)
    pa = PrimaryArea()
    S = read_photoobjs(sdss, wcs, 1./3600., pa=pa,
                       cols=['ra','dec','cmodelflux', 'resolve_status'])
    print 'Read', len(S), 'SDSS objects'

    if ps:
        plt.clf()
        plothist(T.ra, T.dec, 200, imshowargs=dict(cmap='gray'))
        for f in F:
            plt.plot([f.ra0,f.ra0,f.ra1,f.ra1,f.ra0], [f.dec0,f.dec1,f.dec1,f.dec0,f.dec0], 'b-', alpha=0.5)
        plt.title('%s: %i SDSS fields' % (cs82field, len(F)))
        setRadecAxes(ra0,ra1,dec0,dec1)
        ps.savefig()

    decs = np.linspace(dec0, dec1, 1 + opt.decs)
    ras  = np.linspace(ra0,  ra1,  1 + opt.ras)

    print 'Score range:', F.score.min(), F.score.max()
    print 'Before score cut:', len(F)
    F.cut(F.score > 0.5)
    print 'Cut on score:', len(F)

    T.phot_done = np.zeros(len(T), bool)
    T.marginal = np.zeros(len(T), bool)

    # fitstats keys
    fskeys = ['prochi2', 'pronpix', 'profracflux', 'proflux', 'npix']

    # initialize arrays for photometry results
    for band in bands:
        for c in (['sdss_%s_nanomaggies' % band,
                   'sdss_%s_nanomaggies_invvar' % band,
                   'sdss_%s_mag' % band,
                   'sdss_%s_mag_err' % band,] +
                  [k + '_' + band for k in fskeys]):
            T.set(c, np.zeros(len(T), np.float32))
        T.set('fit_ok_%s' % band, np.zeros(len(T), bool))

    for decslice,(dlo,dhi) in enumerate(zip(decs, decs[1:])):
        print 'Dec slice:', dlo, dhi
        for raslice,(rlo,rhi) in enumerate(zip(ras, ras[1:])):
            print 'RA slice:', rlo, rhi

            tslice0 = Time()

            # in deg
            margin = 15. / 3600.
            Ibox = np.flatnonzero(
                ((T.dec + margin) >= dlo) * ((T.dec - margin) <= dhi) *
                ((T.ra  + margin) >= rlo) * ((T.ra  - margin) <= rhi))
            T.marginal[:] = False
            T.marginal[Ibox] = np.logical_not(
                (T.dec[Ibox] >= dlo) * (T.dec[Ibox] <= dhi) *
                (T.ra [Ibox] >= rlo) * (T.ra [Ibox] <= rhi))
            print len(Ibox), 'sources in RA,Dec slice'
            print len(np.flatnonzero(T.marginal)), 'are in the margins'

            Fi = F[np.logical_not(np.logical_or(F.dec0 > dhi, F.dec1 < dlo)) *
                   np.logical_not(np.logical_or(F.ra0  > rhi, F.ra1  < rlo))]
            print len(Fi), 'fields in RA,Dec slice'

            if False:
                plt.clf()
                if len(Ibox):
                    plothist(T.ra[Ibox], T.dec[Ibox], 200,
                             range=((rlo,rhi),(dlo,dhi)),
                             imshowargs=dict(cmap='gray'))
                plt.plot([rlo,rlo,rhi,rhi,rlo], [dlo,dhi,dhi,dlo,dlo], 'r-')
                for f in Fi:
                    plt.plot([f.ra0,f.ra0,f.ra1,f.ra1,f.ra0],
                             [f.dec0,f.dec1,f.dec1,f.dec0,f.dec0],
                             'b-', alpha=0.5)
                plt.title('%s slice d%i r%i: %i SDSS fields' %
                          (cs82field, decslice, raslice, len(Fi)))
                setRadecAxes(rlo,rhi,dlo,dhi)
                ps.savefig()

            print 'Creating Tractor sources...'
            maglim = 24
            cat,icat = get_cs82_sources(T[Ibox], maglim=maglim, bands=bands)
            print 'Got', len(cat), 'sources'
            # Icat: index into T, row-parallel to cat
            Icat = Ibox[icat]
            del icat
            print len(Icat), 'sources created'

            # Match to SDSS sources only for those catalog objects
            # that haven't already been photometered in a previous
            # slice, because we're going to use the SDSS matches to
            # set the initial fluxes.

            # For 'cat' objects, should we set the flux?
            setflux = np.logical_not(T.phot_done[Icat])
            
            # Get SDSS sources to fill in holes....?
            # Sin = S[((S.dec + margin) >= dlo) * ((S.dec - margin) <= dhi) *
            # ((S.ra  + margin) >= rlo) * ((S.ra  - margin) <= rhi)]
            
            print 'Matching to SDSS sources...'
            print 'N SDSS', len(S)
            I,J,d = match_radec(T.ra[Icat], T.dec[Icat], S.ra, S.dec,
                                1./3600., nearest=True)
            print 'found', len(I), 'matches'
            # initialize fluxes based on SDSS matches -- useful for "minsb" approx.
            for i,j in zip(I, J):
                if not setflux[i]:
                    continue
                for band in bands:
                    bi = 'ugriz'.index(band)
                    setattr(cat[i].getBrightness(), band, S.cmodelflux[j, bi])
            del setflux
            del J
            del d

            # index into cat of sources to freeze
            Ifreeze = np.flatnonzero(T.marginal[Icat])
            print 'Freezing', len(Ifreeze), 'sources'
            # index into cat of sources to thaw
            #Ithaw = np.flatnonzero(np.logical_not(T.marginal[Icat]))
            # index into T of sources being fit
            Ifit = Icat[np.flatnonzero(np.logical_not(T.marginal[Icat]))]
            print len(Ifit), 'sources being fit'

            if ps:
                # Create a fake WCS for this subregion -- for plots only
                pixscale = 0.4/3600.
                decpix = int(np.ceil((dhi - dlo) / pixscale))
                # HACK -- ignoring cos(dec)
                rapix = int(np.ceil((rhi - rlo) / pixscale))
                wcs = Tan((rlo+rhi)/2., (dlo+dhi)/2., rapix/2 + 1, decpix/2 + 1,
                          pixscale, 0., 0., pixscale, rapix, decpix)
                # plot sources
                plt.clf()
                p1 = plt.plot(T.ra[Icat], T.dec[Icat], 'k.')
                p1b = plt.plot(T.ra [Icat[T.phot_done[Icat]]],
                               T.dec[Icat[T.phot_done[Icat]]], 'r.')
                p2 = plt.plot(T.ra[Icat[I]], T.dec[Icat[I]], 'o',
                              mec='r', mfc='none')
                p3 = plt.plot(S.ra, S.dec, 'rx')
                p4 = plt.plot(T.ra[Ifit], T.dec[Ifit], 'o', mec='g', mew=1.5,
                              mfc='none', ms=8)
                plt.plot([rlo,rlo,rhi,rhi,rlo], [dlo,dhi,dhi,dlo,dlo], 'r-')   
                m = 0.003
                plt.title('ra slice %i, dec slice %i' % (raslice, decslice))
                plt.figlegend([p1[0],p1b[0],p2[0],p3[0],p4[0]],
                              ('Cat', 'Phot done', 'Matched to SDSS', 'SDSS', 'To fit'),
                              'upper right')
                setRadecAxes(rlo-m,rhi+m,dlo-m,dhi+m)
                ps.savefig()
            
            for band in bands:
                cat.freezeParamsRecursive('*')
                cat.thawPathsTo(band)

                # Now freeze sources in the margins.
                print 'Before freezing marginal sources:',
                print len(cat.getParams()), 'params'
                for i in Ifreeze:
                    cat.freezeParam(i)
                print 'After freezing marginal sources:',
                print len(cat.getParams()), 'params'

                tb0 = Time()

                tims = []
                sigs = []
                npix = 0
                for i,(r,c,f) in enumerate(zip(Fi.run, Fi.camcol, Fi.field)):
                    print 'Reading', (i+1), 'of', len(Fi), ':', r,c,f,band
                    tim,inf = get_tractor_image_dr9(
                        r, c, f, band, sdss=sdss,
                        nanomaggies=True, zrange=[-2,5],
                        roiradecbox=[rlo,rhi,dlo,dhi],
                        invvarIgnoresSourceFlux=True)

                    if tim is None:
                        continue

                    (H,W) = tim.shape
                    print 'Tim', tim.shape
                    tim.wcs.setConstantCd(W/2., H/2.)
                    del tim.origInvvar
                    del tim.starMask
                    del tim.mask
                    # needed for optimize_forced_photometry with rois
                    #del tim.invvar
                    tims.append(tim)
                    sigs.append(1./np.sqrt(np.median(tim.invvar)))
                    npix += (H*W)
                    print 'got', (H*W), 'pixels, total', npix
                    print 'Read image', i+1, 'in band', band, ':', Time()-tb0

                print 'Read', len(tims), 'images'
                print 'total of', npix, 'pixels'

                if ps:
                    coadd = np.zeros((wcs.imageh, wcs.imagew), np.float32)
                    ncoadd = np.zeros((wcs.imageh, wcs.imagew), np.int32)
                    for tim in tims:
                        (H,W) = tim.shape
                        try:
                            wcswrap = AsTransWrapper(tim.wcs.astrans, W,H,
                                                     tim.wcs.x0, tim.wcs.y0)
                            Yo,Xo,Yi,Xi,nil = resample_with_wcs(
                                wcs, wcswrap, [], 3)
                        except:
                            import traceback
                            print 'Failed to resample:'
                            traceback.print_exc()
                            continue
                        coadd[Yo,Xo] += tim.getImage()[Yi,Xi]
                        ncoadd[Yo,Xo] += 1
                    coadd = coadd / np.maximum(1, ncoadd).astype(np.float32)
                    print len(tims), 'tims; ncoadd range %i %i; coadd range %g, %g' % (ncoadd.min(), ncoadd.max(), coadd.min(), coadd.max())
                    plt.clf()
                    coa = dict(interpolation='nearest', origin='lower',
                               extent=[rlo,rhi,dlo,dhi], vmin=-0.05, vmax=0.5)
                    plt.imshow(coadd, **coa)
                    m = 0.003
                    plt.title('coadd: ra slice %i, dec slice %i'%
                              (raslice, decslice))
                    setRadecAxes(rlo-m,rhi+m,dlo-m,dhi+m)
                    ps.savefig()
                
                if False:
                    plt.clf()
                    plothist(Ti.ra, Ti.dec, 200, imshowargs=dict(cmap='gray'))
                    plt.plot([rlo,rlo,rhi,rhi,rlo], [dlo,dhi,dhi,dlo,dlo], 'r-')
                    for tim in tims:
                        H,W = tim.shape
                        rd0 = tim.getWcs().pixelToPosition(0,0)
                        rd1 = tim.getWcs().pixelToPosition(W-1,H-1)
                        plt.plot([rd0.ra,rd0.ra,rd1.ra,rd1.ra,rd0.ra], 
                                 [rd0.dec,rd1.dec,rd1.dec,rd0.dec,rd0.dec],
                                 'b-', alpha=0.5)
                    plt.title('%s slice d%i r%i: %i SDSS fields' %
                              (cs82field, decslice, raslice, len(tims)))
                    ps.savefig()

                sig1 = np.median(sigs)
                minsig = 0.1
                minsb= minsig * sig1
                print 'Sigma1:', sig1, 'minsig', minsig, 'minsb', minsb
                
                tractor = Tractor(tims, cat)
                tractor.freezeParam('images')
                sz = 8
                wantims = (ps is not None)

                tp0 = Time()
                print 'Starting forced phot:', Time()-tb0
                print '(since start of band)'

                R = tractor.optimize_forced_photometry(
                    minsb=minsb, mindlnp=1., wantims=wantims,
                    fitstats=True, variance=True,
                    shared_params=False, use_ceres=True,
                    BW=sz, BH=sz)

                print 'Forced phot finished:', Time()-tb0

                IV = R.IV
                fitstats = R.fitstats
                nm = np.array([src.getBrightness().getBand(band)
                               for src in tractor.getCatalog().getThawedSources()]
                              ).astype(np.float32)
                nm_ivar = IV.astype(np.float32)

                tag = ''
                X = T.get('sdss_%s_nanomaggies%s' % (band, tag))
                X[Ifit] = nm
                X = T.get('sdss_%s_nanomaggies_invvar%s' % (band, tag))
                X[Ifit] = nm_ivar
                dnm = 1./np.sqrt(nm_ivar)
                mag = NanoMaggies.nanomaggiesToMag(nm)
                dmag = np.abs((-2.5 / np.log(10.)) * dnm / nm)
                X = T.get('sdss_%s_mag%s' % (band, tag))
                X[Ifit] = mag
                X = T.get('sdss_%s_mag_err%s' % (band, tag))
                X[Ifit] = dmag
                if fitstats is not None:
                    for k in fskeys:
                        X = T.get(k + '_' + band + tag)
                        X[Ifit] = getattr(fitstats, k).astype(np.float32)

                stat = R.ceres_status
                func_tol = (stat['termination'] == 2)
                steps = stat['steps_successful']
                X = T.get('fit_ok_%s%s' % (band, tag))
                X[Ifit] = np.array([(func_tol and steps > 0)] * len(T))
                         
                if wantims:
                    ims0 = R.ims0
                    ims1 = R.ims1

                    nims = len(tims)
                    cols = int(np.ceil(np.sqrt(nims)))
                    rows = int(np.ceil(nims / float(cols)))

                    #for imnum,ims in enumerate([ims0, ims1]):
                    for imnum,ims in [(1,ims1)]:
                        coadd = np.zeros((wcs.imageh, wcs.imagew), np.float32)
                        ncoadd = np.zeros((wcs.imageh, wcs.imagew), np.int32)
                        for i,(im,mod,ie,chi,roi) in enumerate(ims):
                            tim = tims[i]
                            (H,W) = tim.shape
                            try:
                                wcswrap = AsTransWrapper(
                                    tim.wcs.astrans, W,H, tim.wcs.x0, tim.wcs.y0)
                                Yo,Xo,Yi,Xi,nil = resample_with_wcs(
                                    wcs, wcswrap, [], 3)
                            except:
                                import traceback
                                print 'Failed to resample:'
                                traceback.print_exc()
                                continue
                            coadd[Yo,Xo] += mod[Yi,Xi]
                            ncoadd[Yo,Xo] += 1
                        coadd = coadd / np.maximum(1, ncoadd).astype(np.float32)
                        plt.clf()
                        plt.imshow(coadd, **coa)
                        m = 0.003
                        plt.title('mod%i: ra slice %i, dec slice %i'%
                                  (imnum, raslice, decslice))
                        setRadecAxes(rlo-m,rhi+m,dlo-m,dhi+m)
                        ps.savefig()
                    
                    plt.clf()
                    for i,tim in enumerate(tims):
                        plt.subplot(rows, cols, i+1)
                        ima = dict(interpolation='nearest', origin='lower',
                                   vmin=tim.zr[0], vmax=tim.zr[1], cmap='gray')
                        img = tim.getImage()
                        plt.imshow(img, **ima)
                        plt.xticks([]); plt.yticks([])
                        plt.title(tim.name)
                    plt.suptitle('Data: SDSS %s' % band)
                    ps.savefig()

                    plt.clf()
                    for i,(im,mod,ie,chi,roi) in enumerate(ims1):
                        plt.subplot(rows, cols, i+1)
                        ima = dict(interpolation='nearest', origin='lower',
                                   vmin=tim.zr[0], vmax=tim.zr[1], cmap='gray')
                        plt.imshow(mod, **ima)
                        plt.xticks([]); plt.yticks([])
                        plt.title(tims[i].name)
                    plt.suptitle('Models: SDSS %s' % band)
                    ps.savefig()
                    del ims0
                    del ims1

                del R
                del tims
                del tractor

            # All bands done for this slice.
                
            print 'Slice:', Time()-tslice0
            print 'Total:', Time()-t0

            T.phot_done[Ifit] = True
            
            fn = ('%s-phot-%s-slice%i.fits' %
                  (opt.prefix, cs82field, decslice * (len(ras)-1) + raslice))
            T.writeto(fn)
            T.about()
            print 'Wrote', fn
            Tdone = T[T.phot_done]
            fn = ('%s-phot-%s-slice%i-cut.fits' %
                  (opt.prefix, cs82field, decslice * (len(ras)-1) + raslice))
            Tdone.writeto(fn)
            Tdone.about()
            del Tdone
            print 'Wrote', fn

    T.delete_column('marginal')
    T.delete_column('alphamodel_j2000')
    T.delete_column('deltamodel_j2000')

    fn = '%s-phot-%s.fits' % (opt.prefix, cs82field)
    T.writeto(fn)
    print 'Wrote', fn
    return

if __name__ == '__main__':
    import optparse
    Time.add_measurement(MemMeas)
    sdss = DR9()
    url = sdss.dasurl
    parser = optparse.OptionParser('%prog [options]')
    parser.add_option('-b', dest='bands', type=str, default='ugriz',
                      help='SDSS bands (default %default)')
    parser.add_option('-l', dest='local', action='store_true', default=False,
                      help='Use local SDSS tree?')
    parser.add_option('--das', default=url,
                      help='SDSS DAS url: default %default')
    parser.add_option('--prefix', default='cs82',
                      help='Filename prefix for plots and outputs')
    parser.add_option('--plots', action='store_true', default=False,
                      help='Create plots of results?')

    parser.add_option('--decs', type='int', default=1,
                      help='Number of Dec slices')
    parser.add_option('--ras', type='int', default=40,
                      help='Number of RA slices')

    opt,args = parser.parse_args()

    #lvl = logging.INFO
    lvl = logging.DEBUG
    logging.basicConfig(level=lvl, format='%(message)s', stream=sys.stdout)

    cs82field = 'S82p18p'
    T = main(opt, cs82field)
    
