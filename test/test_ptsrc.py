import matplotlib
matplotlib.use('Agg')
import pylab as plt
import numpy as np

from tractor import *
from tractor.galaxy import *
from astrometry.util.util import Tan
from astrometry.util.plotutils import *

def main():
    ps = PlotSequence('test-ps')
    
    W,H = 100,100
    pixscale = 0.4/3600.
    rot = np.deg2rad(10.)

    psf=GaussianMixturePSF(np.array([0.8, 0.2]),
                           np.zeros((2,2)),
                           np.array([[[6.,0.],[0.,6.]], [[18.,0.],[0.,18.]]]))
    

    tim = Image(data=np.zeros((H,W), np.float32), psf=psf)

    src = ExpGalaxy(PixPos(50,50), Flux(100.), EllipseESoft(1., 0., 0.5))

    tiny = 1e-6

    # mv = 1e-3
    # tim.modelMinval = mv
    # patch0 = src.getModelPatch(tim)
    # sys.exit(0)
    
    for mv in [0., 1e-3, 1e-5]:
        tim.modelMinval = mv

        get_galaxy_cache().clear()
        
        print
        print 'minval =', mv
        print
        
        patch0 = src.getModelPatch(tim)

        p = patch0.patch
        print 'patch smallest non-zero value:', np.min(p[p > 0])

        plt.clf()
        im = np.log10(patch0.patch + tiny)
        mn,mx = im.min(), im.max()
        dimshow(im, extent=patch0.getExtent(), vmin=mn, vmax=mx)
        plt.axis([0,W,0,H])
        plt.title('patch, mv=%f' % mv)
        plt.colorbar()
        ps.savefig()

        for clip in []:#(40,50,50,60), (0,40,0,40), (40,100,60,70)]:
            pp = patch0.copy()
            pp.clipToRoi(*clip)
            plt.clf()
            dimshow(np.log10(pp.patch + tiny), extent=pp.getExtent(),
                    vmin=mn, vmax=mx)
            plt.axis([0,W,0,H])
            plt.title('clipped patch, mv=%f' % mv)
            plt.colorbar()
            ps.savefig()

        
        derivs = src.getParamDerivatives(tim)
        for deriv in derivs:
            if deriv is None:
                continue
            print
            print deriv.name
            print 'Deriv :', deriv.getExtent()
            print 'Patch0:', patch0.getExtent()
            print
            plt.clf()
            deriv.patch[deriv.patch == 0] = np.nan
            dimshow(deriv.patch, extent=deriv.getExtent())
            plt.axis([0,W,0,H])
            plt.title('%s, mv=%f' % (deriv.name, mv))
            ps.savefig()

    
    src.pos.setParams([10,50])

    patch0 = src.getModelPatch(tim)
    plt.clf()
    dimshow(patch0.patch, extent=patch0.getExtent())
    plt.axis([0,W,0,H])
    ps.savefig()

    derivs = src.getParamDerivatives(tim)
    for deriv in derivs:
        if deriv is None:
            continue
        print
        print deriv.name
        print 'Deriv :', deriv.getExtent()
        print 'Patch0:', patch0.getExtent()
        print
        plt.clf()
        dimshow(deriv.patch, extent=deriv.getExtent())
        plt.axis([0,W,0,H])
        ps.savefig()

    


    
    for cd in [
            (-pixscale*np.cos(rot), pixscale*np.sin(rot),
             pixscale*np.sin(rot),  pixscale*np.cos(rot),
             ),
             (-2.11357712641E-07,
              7.32269335496E-05 ,
              -7.32016721769E-05,
              -1.88067009846E-07),]:
    
        wcs = Tan(*[0., 0., W/2., H/2.] + list(cd) + [float(W), float(H)])
    
        ptsrc = PointSource(RaDecPos(0., 0.), Flux(100.))
    
        tim = Image(data=np.zeros((H,W), np.float32), wcs=ConstantFitsWcs(wcs),
                    psf=psf)
        tim.modelMinval = 1e-8
    
        ax = [0, W, 0, H]
        derivs = ptsrc.getParamDerivatives(tim, fastPosDerivs=False)
        print 'Derivs:', derivs
        rows,cols = 2,2
        plt.clf()
        for i,deriv in enumerate(derivs):
            plt.subplot(rows,cols,i+1)
            dimshow(deriv.patch, extent=deriv.getExtent())
            plt.axis(ax)
            plt.title('Orig ' + deriv.name)
            plt.colorbar()
        ps.savefig()
    
        derivs = ptsrc.getParamDerivatives(tim)
        print 'Derivs:', derivs
        plt.clf()
        for i,deriv in enumerate(derivs):
            plt.subplot(rows,cols,i+1)
            dimshow(deriv.patch, extent=deriv.getExtent())
            plt.axis(ax)
            plt.title('Fast ' + deriv.name)
            plt.colorbar()
        ps.savefig()
    
    fsrc = FixedCompositeGalaxy(RaDecPos(0., 0.), Flux(100.), 0.25,
                                EllipseESoft(1., 0., 0.2),
                                EllipseESoft(1., 0., -0.2))

    csrc = CompositeGalaxy(RaDecPos(0., 0.),
                           Flux(100.), EllipseESoft(1., 0., 0.2),
                           Flux(100.), EllipseESoft(1., 0., -0.2))
    
    d = DevGalaxy(fsrc.pos, fsrc.brightness, fsrc.shapeDev)
    e = ExpGalaxy(fsrc.pos, fsrc.brightness, fsrc.shapeExp)

    dd = d.getParamDerivatives(tim)
    de = e.getParamDerivatives(tim)
    dcomp = fsrc.getParamDerivatives(tim)
    f = fsrc.fracDev.getClippedValue()
    print 'de before:', np.sum(np.abs(de[0].patch))
    print 'dd before:', np.sum(np.abs(dd[0].patch))
    for deriv in de:
        if deriv is not None:
            deriv *= (1.-f)
    for deriv in dd:
        if deriv is not None:
            deriv *= f
    print 'de after:', np.sum(np.abs(de[0].patch))
    print 'dd after:', np.sum(np.abs(dd[0].patch))
            
    e2 = ExpGalaxy(fsrc.pos, fsrc.brightness * (1.-f), fsrc.shapeExp)
    d2 = DevGalaxy(fsrc.pos, fsrc.brightness *     f , fsrc.shapeDev)
    de2 = e2.getParamDerivatives(tim)
    dd2 = d2.getParamDerivatives(tim)
    
    print 'de2:', np.sum(np.abs(de2[0].patch))
    print 'dd2:', np.sum(np.abs(dd2[0].patch))
    
    if True:
        plt.clf()
        mx = np.max(np.abs(dcomp[0].patch))
        plt.subplot(3,3,1)
        dimshow(dcomp[0].patch, vmin=-mx, vmax=mx)
        plt.title('FixedComp')
    
        plt.subplot(3,3,4)
        dimshow(de[0].patch, vmin=-mx, vmax=mx)
        plt.title('exp')
    
        plt.subplot(3,3,5)
        dimshow(dd[0].patch, vmin=-mx, vmax=mx)
        plt.title('deV')
        
        plt.subplot(3,3,2)
        dimshow((dd[0] + de[0]).patch, vmin=-mx, vmax=mx)
        plt.title('sum')
    
        plt.subplot(3,3,7)
        dimshow(de2[0].patch, vmin=-mx, vmax=mx)
        plt.title('exp2')
    
        plt.subplot(3,3,8)
        dimshow(dd2[0].patch, vmin=-mx, vmax=mx)
        plt.title('deV2')
        
        plt.subplot(3,3,9)
        dimshow((dd2[0] + de2[0]).patch, vmin=-mx, vmax=mx)
        plt.title('sum2')
        
        plt.subplot(3,3,3)
        ss = fsrc.getStepSizes()
        p0 = fsrc.getParams()
        patch0 = fsrc.getModelPatch(tim)
        i=0
        s = ss[i]
        oldval = fsrc.setParam(i, p0[i]+s)
        patchx = fsrc.getModelPatch(tim)
        fsrc.setParam(i, p0[i])
        dp = (patchx - patch0) / s
        dimshow(dp.patch, vmin=-mx, vmax=mx)
        plt.title('step')
        
        ps.savefig()
    
        
    for src,tt in [(csrc,'Comp'), (fsrc,'FixedComp'), (e,'E'), (d,'D'),
                   (e2,'E2'), (d2,'D2')]:
        print

        patch0 = src.getModelPatch(tim)
        derivs = src.getParamDerivatives(tim)
        print tt, ':', np.sum(np.abs(derivs[0].patch))
        cols = int(np.ceil(np.sqrt(len(derivs))))
        rows = int(np.ceil(float(len(derivs)) / cols))
        plt.clf()
        maxes = []
        for i,deriv in enumerate(derivs):
            plt.subplot(rows,cols,i+1)

            print
            print deriv.name
            print 'Deriv :', deriv.getExtent()
            print 'Patch0:', patch0.getExtent()
            print
                        
            mx = max(np.abs(deriv.patch.min()), deriv.patch.max())
            dimshow(deriv.patch, extent=deriv.getExtent(), vmin=-mx, vmax=mx)
            maxes.append(mx)
            plt.axis(ax)
            plt.title(deriv.name, fontsize=8)
            #plt.colorbar()
            plt.xticks([]); plt.yticks([])    
        plt.suptitle('getParamDerivatives: %s' % tt)
        ps.savefig()
        
        patch0 = src.getModelPatch(tim)
        print 'Patch sum:', patch0.patch.sum()    
    
        p0 = src.getParams()
        ss = src.getStepSizes()
        names = src.getParamNames()
        plt.clf()
        for i,(s,name) in enumerate(zip(ss, names)):
            plt.subplot(rows,cols,i+1)
        
            oldval = src.setParam(i, p0[i]+s)
            patchx = src.getModelPatch(tim)
            src.setParam(i, p0[i])
        
            dp = (patchx - patch0) / s
    
            if i == 0:
                print tt, 'stepping:', np.sum(np.abs(dp.patch))
            
            dimshow(dp.patch, extent=dp.getExtent(), vmin=-maxes[i], vmax=maxes[i])
            plt.axis(ax)
            plt.title(name, fontsize=8)
            #plt.colorbar()
            plt.xticks([]); plt.yticks([])    
        plt.suptitle('Stepping parameters: %s' % tt)
        ps.savefig()
    
    
    
    
    if True:
        i=0
        ss = fsrc.getStepSizes()
        p0 = fsrc.getParams()
        patch0 = fsrc.getModelPatch(tim)
        s = ss[i]
        oldval = fsrc.setParam(i, p0[i]+s)
        patchx = fsrc.getModelPatch(tim)
        fsrc.setParam(i, p0[i])
        dp = (patchx - patch0) / s
        
        print 'Step FixedComp: %12.1f' % np.sum(np.abs(dp.patch))
        print 'FixedComp:      %12.1f' % np.sum(np.abs(dcomp[i].patch))
        print 'E component:    %12.1f' % np.sum(np.abs(de[i].patch))
        print 'D component:    %12.1f' % np.sum(np.abs(dd[i].patch))
        print 'E+D:            %12.1f' % np.sum(np.abs((dd[i] + de[i]).patch))
        print 'E2:             %12.1f' % np.sum(np.abs(de2[i].patch))
        print 'D2:             %12.1f' % np.sum(np.abs(dd2[i].patch))
        print 'E2+D2:          %12.1f' % np.sum(np.abs((dd2[i] + de2[i]).patch))
    
    
        
if __name__ == '__main__':
    main()
    
