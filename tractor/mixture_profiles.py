# Copyright 2011 David W. Hogg and Dustin Lang.  All rights reserved.
if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')
    import pylab as plt
    import matplotlib.cm as cm
import numpy as np
#import scipy.spatial.distance as scp

try:
    from . import mp_fourier
except:
    mp_fourier = None

from patch import Patch
    
# magic arrays, generated by running optimize_mixture_profiles.py:
# (note optimize_mixture_profiles.py now lives in Hogg's TheTractor github repo)
exp_amp = np.array([  2.34853813e-03,   3.07995260e-02,   2.23364214e-01,
              1.17949102e+00,   4.33873750e+00,   5.99820770e+00])
exp_var = np.array([  1.20078965e-03,   8.84526493e-03,   3.91463084e-02,
              1.39976817e-01,   4.60962500e-01,   1.50159566e+00])
exp_amp /= np.sum(exp_amp)

dev_amp = np.array([  4.26347652e-02,   2.40127183e-01,   6.85907632e-01,   1.51937350e+00,
              2.83627243e+00,   4.46467501e+00,   5.72440830e+00,   5.60989349e+00])
dev_var = np.array([  2.23759216e-04,   1.00220099e-03,   4.18731126e-03,   1.69432589e-02,
              6.84850479e-02,   2.87207080e-01,   1.33320254e+00,   8.40215071e+00])
dev_amp /= np.sum(dev_amp)

def get_exp_mixture():
    return MixtureOfGaussians(exp_amp, np.zeros((exp_amp.size, 2)), exp_var)

def get_dev_mixture():
    return MixtureOfGaussians(dev_amp, np.zeros((dev_amp.size, 2)), dev_var)

class MixtureOfGaussians():

    # symmetrize is an unnecessary step in principle, but in practice?
    def __init__(self, amp, mean, var):
        '''
        amp: shape (K,)
        mean: shape (K,D)
        var: shape (K,D,D)
        '''
        self.amp = np.atleast_1d(amp).astype(float)
        self.mean = np.atleast_2d(np.array(mean)).astype(float)
        (self.K, self.D) = self.mean.shape
        self.set_var(var)
        self.symmetrize()
        #self.test()

    def __str__(self):
        result = "MixtureOfGaussians instance"
        result += " with %d components in %d dimensions:\n" % (self.K, self.D)
        result += " amp  = %s\n" % self.amp.__str__()
        result += " mean = %s\n" % self.mean.__str__()
        result += " var  = %s\n" % self.var.__str__()
        return result

    def set_var(self, var):
        if var.size == self.K:
            self.var = np.zeros((self.K, self.D, self.D))
            for d in range(self.D):
                self.var[:,d,d] = var
        else:
            self.var = np.array(var).astype(float)

    def symmetrize(self):
        for i in range(self.D):
            for j in range(i):
                tmpij = 0.5 * (self.var[:,i,j] + self.var[:,j,i])
                self.var[:,i,j] = tmpij
                self.var[:,j,i] = tmpij

    # very harsh testing, and expensive
    def test(self):
        assert(self.amp.shape == (self.K, ))
        assert(self.mean.shape == (self.K, self.D))
        assert(self.var.shape == (self.K, self.D, self.D))
        for k in range(self.K):
            thisvar = self.var[k]
            assert(np.sum(thisvar.T - thisvar) == 0.)
            assert(np.linalg.det(thisvar) >= 0.)

    def copy(self):
        return MixtureOfGaussians(self.amp, self.mean, self.var)

    def normalize(self):
        self.amp /= np.sum(self.amp)

    def extend(self, other):
        assert(self.D == other.D)
        self.K = self.K + other.K
        self.amp  = np.append(self.amp,  other.amp)
        self.mean = np.append(self.mean, other.mean, axis=0)
        self.var  = np.append(self.var,  other.var , axis=0)
        assert(self.amp.shape  == (self.K,))
        assert(self.mean.shape == (self.K, self.D))
        assert(self.var.shape  == (self.K, self.D, self.D))
        
    def __add__(self, other):
        assert(self.D == other.D)
        D = self.D
        K = self.K + other.K
        amp  = np.append(self.amp,  other.amp)
        mean = np.append(self.mean, other.mean, axis=0)
        var  = np.append(self.var,  other.var , axis=0)
        assert(amp.shape  == (K,))
        assert(mean.shape == (K, D))
        assert(var.shape  == (K, D, D))
        s = MixtureOfGaussians(amp, mean, var)
        s.normalize()
        return s
        
    def apply_affine(self, shift, scale):
        '''
        shift: D-vector offset
        scale: DxD-matrix transformation
        '''
        assert(shift.shape == (self.D,))
        assert(scale.shape == (self.D, self.D))
        newmean = self.mean + shift
        newvar = np.zeros_like(self.var)
        for k in range(self.K):
            newvar[k,:,:] = np.dot(scale.T, np.dot(self.var[k,:,:], scale))
        return MixtureOfGaussians(self.amp.copy(), newmean, newvar)

    # dstn: should this be called "correlate"?
    def convolve(self, other):
        assert(self.D == other.D)
        newK = self.K * other.K
        D = self.D
        newamp = np.zeros((newK))
        newmean = np.zeros((newK, D))
        newvar = np.zeros((newK, D, D))
        newk = 0
        for k in range(other.K):
            nextnewk = newk + self.K
            newamp[newk:nextnewk] = self.amp * other.amp[k]
            newmean[newk:nextnewk,:] = self.mean + other.mean[k]
            newvar[newk:nextnewk,:,:] = self.var + other.var[k]
            newk = nextnewk
        return MixtureOfGaussians(newamp, newmean, newvar)

    def getFourierTransform(self, w, v, use_mp_fourier=True):
        if mp_fourier and use_mp_fourier:
            f = mp_fourier.mixture_profile_fourier_transform(
                self.amp, self.mean, self.var, w, v)
            return f

        Fsum = None

        if not hasattr(self, 'ivar'):
            self.ivar = np.zeros((self.K, self.D, self.D))
            for k in range(self.K):
                self.ivar[k,:,:] = np.linalg.inv(self.var[k,:,:])

        for k in range(self.K):
            #V = self.var[k,:,:]
            #iv = np.linalg.inv(V)
            iv = self.ivar[k,:,:]
            mu = self.mean[k,:]
            amp = self.amp[k]
            a,b,d = 0.5 * iv[0,0], 0.5 * iv[0,1], 0.5 * iv[1,1]
            det = a*d - b**2
            F = (np.exp(-np.pi**2/det *
                        (a * v[:,np.newaxis]**2 +
                         d * w[np.newaxis,:]**2 -
                         2*b*v[:,np.newaxis]*w[np.newaxis,:]))
                         * np.exp(-2.*np.pi* 1j *(mu[0]*w[np.newaxis,:] + 
                                                  mu[1]*v[:,np.newaxis])))

            if Fsum is None:
                Fsum = amp * F
            else:
                Fsum += amp * F

        return Fsum        
    
    # ideally pos is a numpy array shape (N, self.D)
    # returns a numpy array shape (N)
    # may fail for self.D == 1
    # loopy
    def evaluate_3(self, pos):
        if pos.size == self.D:
            pos = np.reshape(pos, (1, self.D))
        (N, D) = pos.shape
        assert(self.D == D)
        twopitotheD = (2.*np.pi)**self.D
        result = np.zeros(N)
        for k in range(self.K):
            # pos is (N, D)
            # mean[k] is (D,)
            dpos = pos - self.mean[k]
            dsq = np.sum(dpos * np.dot(dpos, np.linalg.inv(self.var[k])), axis=1)
            I = (dsq < 700)
            result[I] += (self.amp[k] / np.sqrt(twopitotheD * np.linalg.det(self.var[k]))) * np.exp(-0.5 * dsq[I])
        return result

    def evaluate_1(self, pos):
        import scipy.spatial.distance as scp
        if pos.size == self.D:
            pos = np.reshape(pos, (1, self.D))
        (N, D) = pos.shape
        assert(self.D == D)
        twopitotheD = (2.*np.pi)**self.D
        result = np.zeros(N)
        for k in range(self.K):
            dsq = scp.cdist(pos, self.mean[np.newaxis, k], 'mahalanobis', VI=np.linalg.inv(self.var[k]))[:,0]**2
            I = (dsq < 700)
            result[I] += (self.amp[k] / np.sqrt(twopitotheD * np.linalg.det(self.var[k]))) * np.exp(-0.5 * dsq[I])
        return result

    def evaluate_2(self, pos):
        from mix import c_gauss_2d
        if pos.size == self.D:
            pos = np.reshape(pos, (1, self.D))
        (N, D) = pos.shape
        assert(self.D == D)
        result = np.zeros(N)
        rtn = c_gauss_2d(pos, self.amp, self.mean, self.var, result)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d failed')
        return result

    def evaluate_grid_dstn(self, x0, x1, y0, y1, cx, cy):
        '''
        [x0,x1): (int) X values to evaluate
        [y0,y1): (int) Y values to evaluate
        (cx,cy): (float) pixel center of the MoG
        '''
        from mix import c_gauss_2d_grid
        assert(self.D == 2)
        result = np.zeros((y1-y0, x1-x0))
        rtn = c_gauss_2d_grid(x0, x1, y0, y1, cx, cy,
                              self.amp, self.mean,self.var, result)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_grid failed')
        return Patch(x0, y0, result)

    def evaluate_grid_approx(self, x0, x1, y0, y1, cx, cy, minval):
        '''
        minval: small value at which to stop evaluating

        [x0,x1): (int) X values to evaluate
        [y0,y1): (int) Y values to evaluate
        (cx,cy): (float) pixel center of the MoG

        Returns: numpy array of shape (y1-y0, x1-x0)
        '''
        from mix import c_gauss_2d_approx2
        assert(self.D == 2)

        result = np.zeros((y1-y0, x1-x0))
        rtn = c_gauss_2d_approx2(x0, x1, y0, y1, cx, cy, minval,
                                 self.amp, self.mean,self.var, result)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_approx2 failed')
        return result

    def evaluate_grid_approx3(self, x0, x1, y0, y1, fx, fy, minval,
                              derivs=False, minradius=3, doslice=True,
                              maxmargin=100):
        '''
        minval: small value at which to stop evaluating

        [x0,x1): (int) X values to evaluate
        [y0,y1): (int) Y values to evaluate
        (fx,fy): (float) pixel offset of the MoG; ie, evaluate MoG shifted by
                this amount.

        'maxmargin': don't render sources more than this distance outside the box.

        If 'doslice' is True, slices the images down to the non-zero
        bounding-box.

        If 'derivs' is True, computes and returns x and y derivatives too.
        
        Unlike evaluate_grid_approx, returns a Patch object.
        '''
        from mix import c_gauss_2d_approx3

        result = np.zeros((y1-y0, x1-x0))
        xderiv = yderiv = mask = None
        if derivs:
            xderiv = np.zeros_like(result)
            yderiv = np.zeros_like(result)

        # guess:
        cx = int(self.mean[0,0] + fx)
        cy = int(self.mean[0,1] + fy)

        if (cx < x0 - maxmargin or cx > x1 + maxmargin or
            cy < y0 - maxmargin or cy > y1 + maxmargin):
            return None #Patch(x0,y0,None)

        try:
            rtn,sx0,sx1,sy0,sy1 = c_gauss_2d_approx3(
                int(x0), int(x1), int(y0), int(y1),
                float(fx), float(fy), float(minval),
                self.amp, self.mean, self.var,
                result, xderiv, yderiv, mask,
                cx, cy, int(minradius))
        except:
            print 'failure calling c_gauss_2d_approx3:'
            print x0, x1, y0, y1
            print fx, fy, minval
            print cx, cy, minradius
            print '-->', int(x0), int(x1), int(y0), int(y1)
            print '-->', float(fx), float(fy), float(minval)
            print '-->', cx, cy, int(minradius)
            raise
        assert(rtn == 0)
        if doslice:
            slc = slice(sy0,sy1),slice(sx0,sx1)
            result = result[slc].copy()
            if derivs:
                xderiv = xderiv[slc].copy()
                yderiv = yderiv[slc].copy()
            x0 += sx0
            y0 += sy0

        if derivs:
            return (Patch(x0,y0,result), Patch(x0,y0,xderiv),
                    Patch(x0,y0,yderiv))

        return Patch(x0,y0,result)
    
    def evaluate_grid_hogg(self, xlo, xhi, ylo, yhi):
        assert(self.D == 2)
        xy = np.array(np.meshgrid(range(xlo, xhi), range(ylo, yhi)))
        D, nx, ny = xy.shape
        xy = xy.reshape((D, nx * ny)).T
        result = self.evaluate_1(xy)
        return result.reshape((nx, ny))

    evaluate = evaluate_2
    #evaluate_grid = evaluate_grid_hogg
    evaluate_grid = evaluate_grid_dstn

def mixture_to_patch(mixture, x0, x1, y0, y1, minval=0., exactExtent=False):
    '''
    `mixture`: a MixtureOfGaussians
    `x0,x1,y0,y1`: integer bounds [x0,x1), [y0,y1) of the grid to evaluate

    Returns: a Patch object
    '''
    if minval == 0. or minval is None:
        return mixture.evaluate_grid(x0, x1, y0, y1, 0., 0.)

    p = mixture.evaluate_grid_approx3(x0, x1, y0, y1, 0., 0., minval,
                                      doslice=not(exactExtent))
    #print 'mixture_to_patch: got extent', [x0,x1,y0,y1], 'returning extent', p.getExtent()
    return p
    
def model_to_patch(model, scale, posmin, posmax):
    xl = np.arange(posmin[0], posmax[0]+1., 1.)
    nx = xl.size
    yl = np.arange(posmin[1], posmax[1]+1., 1.)
    ny = yl.size
    x, y = np.meshgrid(xl, yl)
    dist = np.sqrt(np.ravel(x)**2 + np.ravel(y)**2)
    if model == 'exp':
        return np.reshape(np.exp(-1. * (dist / scale)), (ny, nx))
    if model == 'dev':
        return np.reshape(np.exp(-1. * (dist / scale)**0.25), (ny, nx))
    else:
        return 0.

def functional_test_circular_mixtures():
    exp_mixture = MixtureOfGaussians(exp_amp, np.zeros((exp_amp.size, 2)), exp_var)
    dev_mixture = MixtureOfGaussians(dev_amp, np.zeros((dev_amp.size, 2)), dev_var)
    pos = np.random.uniform(-5.,5.,size=(24,2))
    exp_eva = exp_mixture.evaluate(pos)
    dev_eva = dev_mixture.evaluate(pos)
    (N, D) = pos.shape
    for n in range(N):
        print '(%+6.3f %+6.3f) exp: %+8.5f' % (pos[n,0], pos[n,1], exp_eva[n] - np.exp(-1. * np.sqrt(np.sum(pos[n] * pos[n]))))
        print '(%+6.3f %+6.3f) dev: %+8.5f' % (pos[n,0], pos[n,1], dev_eva[n] - np.exp(-1. * np.sqrt(np.sum(pos[n] * pos[n]))**0.25))

def functional_test_patch_maker(fn, psf=None):
    scale = 30.
    posmin = np.array([-3, -5]) * scale
    posmax = np.array([1, 1]) * scale
    exp_mixture = MixtureOfGaussians(exp_amp*scale*scale, np.zeros((exp_amp.size, 2)), exp_var*scale*scale)

    # Works! exp_mixture.apply_affine(np.array([10,-30]), np.eye(2))
    S = np.array([[1,0],[0,0.5]])
    print 'Det', np.linalg.det(S)
    S /= np.sqrt(np.linalg.det(S))
    print 'Det', np.linalg.det(S)
    r = np.deg2rad(30.)
    cr = np.cos(r)
    sr = np.sin(r)
    S = np.dot(S, np.array([[cr, sr],[-sr, cr]]))
    print 'Det', np.linalg.det(S)
    exp_mixture = exp_mixture.apply_affine(np.array([10,-30]), S)

    if psf is not None:
        exp_mixture = exp_mixture.convolve(psf)
    pa = [int(x) for x in [posmin[0], posmax[0], posmin[1], posmax[1]]]
    exp_mix_patch = mixture_to_patch(exp_mixture, *pa)
    exp_mix_patch = exp_mix_patch.patch
    exp_patch = model_to_patch('exp', scale, posmin, posmax)
    dev_mixture = MixtureOfGaussians(dev_amp*scale*scale, np.zeros((dev_amp.size, 2)), dev_var*scale*scale)
    if psf is not None:
        dev_mixture = dev_mixture.convolve(psf)
    dev_mix_patch = mixture_to_patch(dev_mixture, *pa)
    dev_mix_patch = dev_mix_patch.patch
    dev_patch = model_to_patch('dev', scale, posmin, posmax)
    cmap = cm.gray
    vmin = -0.5
    vmax = 1.0
    factor = 100.
    plt.clf()
    plt.subplot(231)
    plt.imshow(exp_mix_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=vmin, vmax=vmax)
    plt.colorbar()
    plt.subplot(232)
    plt.imshow(exp_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=vmin, vmax=vmax)
    plt.colorbar()
    plt.subplot(233)
    plt.imshow(exp_mix_patch - exp_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=-1./factor, vmax=1./factor)
    plt.colorbar()
    plt.subplot(234)
    plt.imshow(dev_mix_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=vmin, vmax=vmax)
    plt.colorbar()
    plt.subplot(235)
    plt.imshow(dev_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=vmin, vmax=vmax)
    plt.colorbar()
    plt.subplot(236)
    plt.imshow(dev_mix_patch - dev_patch, interpolation='nearest', origin='lower', cmap=cmap, vmin=-1./factor, vmax=1./factor)
    plt.colorbar()
    plt.savefig(fn)

if __name__ == '__main__':
    # functional test: c_gauss_2d_approx

    from mix import c_gauss_2d_approx, c_gauss_2d_grid, c_gauss_2d_approx2, c_gauss_2d_approx3

    from astrometry.util.plotutils import PlotSequence
    ps = PlotSequence('approx')
    
    for j in range(100):
        print
        print 'j =', j
        print

        x0,x1 = -50, 50
        y0,y1 = -51, 51
        W = x1 - x0
        H = y1 - y0
        result = np.zeros((H, W))

        amp = np.array([1.0])
        mean = np.array([[0.3, 0.7],])
        minval = 1e-3
        dx = dy = 0.
        minradius = 2

        if j == 0:
            var = np.array([ [ [ 4., 4. ], [4., 9.,] ], ])
        elif j == 1:
            var = np.array([ [ [ 4., -5.5 ], [-5.5, 9.,] ], ])
        elif j == 2:
            var = np.array([ [ [ 4., 0. ], [0., 9.,] ], ])
        elif j == 3:
            var = np.array([ [ [ 100., 50. ], [50., 100.,] ], ])
            minval = 1e-6
        elif j == 4:
            var = np.array([ [ [ 100., 50. ], [50., 100.,] ], ])
            minval = 1e-9
        elif j in [5,6]:
            var = np.array([ [ [ 400., -100. ], [-100., 100.,] ], ])
            minval = 1e-9
            if j == 6:
                mean[0,0] -= 0.8
        elif j == 7:
            mean += 10.
            #x = 10
            #y = 10
            minval = 1e-9
        elif j == 8:
            mean[0,1] += 50.
            minval = 1e-9
        elif j == 9:
            mean[0,1] += 80.
            minval = 1e-9
        elif j == 10:
            amp = np.array([0.9, 0.1])
            mean = np.array([[0.3, 0.7],[-0.3, -0.7],])
            var = np.array([ [ [ 400., -100. ], [-100., 100.,] ],
                             [ [ 400., -100. ], [-100., 100.,] ], ])
            minval = 1e-9
        elif j == 11:
            amp = np.array([0.99, 0.01])
            mean = np.array([[0.3, 0.7],[-0.3, -0.7],])
            var = np.array([ [ [ 400., -100. ], [-100., 100.,] ],
                             [ [ 400., -100. ], [-100., 100.,] ], ])
            minval = 1e-9
        elif j == 12:
            amp = np.array([0.99, 0.01])
            mean = np.array([[0.3, 0.7],[-0.3, -0.7],])
            var = np.array([ [ [ 400., -100. ], [-100., 100.,] ],
                             [ [ 400., -100. ], [-100., 100.,] ], ])
            dx = 1.
            minval = 1e-9
        elif j == 13:
            var = np.array([ [ [ 4., 4. ], [4., 9.,] ], ])
            minradius = 12
        elif j == 14:
            var = np.array([ [ [ 0.5, -0.6 ], [-0.6, 1.,] ], ])
            minradius = 4
        else:
            break

        print 'args (approx1):', x0, x1, y0, y1, dx, dy, minval, amp, mean, var
        rtn = c_gauss_2d_approx(x0, x1, y0, y1, dx, dy, minval, amp, mean, var, result)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_approx failed')

        r2 = np.zeros((H,W))
        rtn = c_gauss_2d_grid(x0 - dx, 1, W, y0 - dy, 1, H, amp, mean, var, r2)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_grid failed')

        print 'Max difference:', np.max(np.abs(r2 - result))

        plt.clf()
        plt.subplot(1,2,1)
        plt.imshow(np.log10(np.maximum(minval * 1e-3, r2)),
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Grid')
        
        plt.subplot(1,2,2)
        plt.imshow(np.log10(np.maximum(minval * 1e-3, result)),
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Approx 1')
        plt.suptitle('j = %i' % j)
        ps.savefig()

        assert(np.all(np.abs(r2 - result) < minval))

        result2 = np.zeros((H, W))
        print 'args (approx2):', x0, x1, y0, y1, dx, dy, minval, amp, mean, var
        rtn = c_gauss_2d_approx2(x0, x1, y0, y1, dx, dy, minval, amp, mean, var, result2)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_approx2 failed')
        print 'Max difference 2:', np.max(np.abs(r2 - result2))
        
        plt.clf()
        plt.subplot(1,2,1)
        plt.imshow(np.log10(np.maximum(minval * 1e-3, result2)),
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Approx 2')

        plt.subplot(1,2,2)
        plt.imshow(result2 - r2,
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Approx 2 - Grid')
        plt.suptitle('j = %i' % j)
        ps.savefig()

        
        assert(np.all(np.abs(r2 - result2) < minval))

        result3 = np.zeros((H, W))
        xderiv, yderiv, mask = None, None, None
        xc,yc = int(mean[0,0] + dx), int(mean[0,1] + dy)
        args = (x0, x1, y0, y1, dx, dy, minval, amp, mean, var, result3, xderiv, yderiv, mask, xc, yc, minradius)
        print 'args (approx3):', args
        rtn = c_gauss_2d_approx3(*args)
        if rtn == -1:
            raise RuntimeError('c_gauss_2d_approx3 failed')
        print 'Max difference 3:', np.max(np.abs(r2 - result3))

        plt.clf()
        plt.subplot(1,2,1)
        plt.imshow(np.log10(np.maximum(minval * 1e-3, result3)),
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Approx 3')

        plt.subplot(1,2,2)
        plt.imshow(result3 - r2,
                   interpolation='nearest', origin='lower')
        plt.colorbar()
        plt.title('Approx 3 - Grid')
        plt.suptitle('j = %i' % j)
        ps.savefig()
        
        # if j == 3:
        #   plt.clf()
        #   for row in range(50,71):
        #       plt.plot(result[row,:])
        #       dy = (row - S/2)
        #       mx = var[0, 0, 1] / var[0, 1, 1] * dy
        #       plt.axvline(mx + S/2)
        #   ps.savefig()
        

    import sys
    sys.exit(0)


    # functional_test_circular_mixtures()
    psfamp = np.array([0.7,0.2,0.1])
    psfmean = np.zeros((3,2))
    psfvar = np.zeros((3,2,2))
    psfvar[0,0,0] = 1.2**2
    psfvar[0,1,1] = psfvar[0,0,0]
    psfvar[1,0,0] = 2.4**2
    psfvar[1,1,1] = psfvar[1,0,0]
    psfvar[2,0,0] = 3.6**2
    psfvar[2,1,1] = psfvar[2,0,0]
    psf = MixtureOfGaussians(psfamp, psfmean, psfvar)
    functional_test_patch_maker('test_patch.png')
    functional_test_patch_maker('test_psf_patch.png', psf=psf)
