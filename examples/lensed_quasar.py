# Copyright 2011 David W. Hogg (NYU) and Phil Marshall (Oxford).
# All rights reserved (for now).

# to-do
# -----
# - make and draw critical curve
# - make and draw caustic
# - write and test lens equation solver
# - write down priors on magnification / mag bias

import numpy as np
import markovpy as dfm
if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc
    rc('font',**{'family':'serif','serif':'Computer Modern Roman','size':18})
    rc('text', usetex=True)
    import pylab as plt

# cheap and nasty singular isothermal sphere model
# initialize with
# - position        : lens position (same units as einsteinradius)
# - einsteinradius  : Einstein radius (some angular units)
# - gammacos2phi    : external shear amplitude times cos(2 phi)
# - gammasin2phi    : external shear amplitude times sin(2 phi)
class GravitationalLens:
    def __init__(self, position, einsteinradius, gammacos2phi, gammasin2phi):
        self.position = np.array(position)
        self.einsteinradius = einsteinradius
        self.gammacos2phi = gammacos2phi
        self.gammasin2phi = gammasin2phi
        self.gamma = np.sqrt(self.gammacos2phi**2 + self.gammasin2phi**2)
        self.phi = 0.5 * np.arctan2(self.gammasin2phi, self.gammacos2phi)
        self.name = 'singular isothermal sphere'
        return None

    def __str__(self):
        return '%s (%s %f %f %f)' % (self.name, self.position, self.einsteinradius, self.gammacos2phi, self.gammasin2phi)

    def cross(self):
        eig1 = np.array([np.cos(self.phi), np.sin(self.phi)])
        eig2 = np.array([-np.sin(self.phi), np.cos(self.phi)])
        foo = np.array([ eig1 / (1. - self.gamma),
                         eig2 / (1. + self.gamma),
                        -eig1 / (1. - self.gamma),
                        -eig2 / (1. + self.gamma)])
        return self.position + self.einsteinradius * foo

    # input: image positions shape (N, 2)
    # output: source position
    def sourcepositions(self, imagepositions):
        spos = 1. * imagepositions # note copy issue
        dpos = imagepositions - self.position
        r = np.outer(np.sqrt(dpos[:,0] * dpos[:,0] + dpos[:,1] * dpos[:,1]), [1,1])
        spos -= self.einsteinradius * dpos / r
        spos[:,0] -= self.gammacos2phi * dpos[:,0]
        spos[:,0] -= self.gammasin2phi * dpos[:,1]
        spos[:,1] += self.gammacos2phi * dpos[:,1]
        spos[:,1] -= self.gammasin2phi * dpos[:,0]
        return spos

    # output shape (N, 2, 2)
    def inversemagnificationtensors(self, imagepositions):
        mag = np.zeros((len(imagepositions), 2, 2))
        mag[:,0,0] = 1.
        mag[:,1,1] = 1.
        dpos = imagepositions - self.position
        rcubed = ((dpos[:,0] * dpos[:,0]) + (dpos[:,1] * dpos[:,1]))**1.5
        if np.min(rcubed) <= 0.:
            print imagepositions
            print self.position
            print mag
            print self
        assert(np.min(rcubed) > 0.)
        mag[:,0,0] -= self.einsteinradius * dpos[:,1] * dpos[:,1] / rcubed
        mag[:,0,1] += self.einsteinradius * dpos[:,1] * dpos[:,0] / rcubed
        mag[:,1,0] += self.einsteinradius * dpos[:,0] * dpos[:,1] / rcubed
        mag[:,1,1] -= self.einsteinradius * dpos[:,0] * dpos[:,0] / rcubed
        mag[:,0,0] -= self.gammacos2phi
        mag[:,0,1] -= self.gammasin2phi
        mag[:,1,0] -= self.gammasin2phi
        mag[:,1,1] += self.gammacos2phi
        return mag

    def magnificationtensors(self, imagepositions):
        return np.array([np.linalg.inv(t) for t in self.inversemagnificationtensors(imagepositions)])

    # APPROXIMATION: Not yet marginalizing over true source position, true source flux
    # look for "WRONG" in code
    # Note magnification sign insanity
    def ln_prior(self, imagepositions, imagefluxes, positionvariance, fluxvariance, paritycheck=True):
        def ln_Gaussian_1d_zeromean(x, var):
            return -0.5 * np.log(2. * np.pi * var) - 0.5 * x**2 / var
        assert(len(imagepositions) == 4)
        sourcepositions = self.sourcepositions(imagepositions)
        meansourceposition = np.mean(sourcepositions, axis=0) # WRONG
        magtensors = self.magnificationtensors(imagepositions)
        mags = np.array(map(np.linalg.det, magtensors))
        if paritycheck:
            if mags[0] <= 0.:
                return -np.Inf
            if mags[1] >= 0.:
                return -np.Inf
            if mags[2] <= 0.:
                return -np.Inf
            if mags[3] >= 0.:
                return -np.Inf
        dimagepositions = np.array([np.dot(tens, (spos - meansourceposition)) for tens, spos in zip(magtensors, sourcepositions)])
        return np.sum(ln_Gaussian_1d_zeromean(dimagepositions, positionvariance))

    def sample_prior(self, imagepositions, imagefluxes, positionvariance, fluxvariance, nlink):
        def lnp(pars):
            return self.ln_prior(np.reshape(pars[:8], (4, 2)), None, positionvariance, None)
        pars = np.ravel(imagepositions)
        nwalkers = 100
        ndim     = len(pars)
        initial_position = [pars + 0.01 * np.random.normal(size=ndim) for i in xrange(nwalkers)]
        sampler = dfm.EnsembleSampler(nwalkers, ndim, lnp)
        pos, prob, state = sampler.run_mcmc(initial_position, None, nlink)
        print 'Mean acceptance fraction: ',np.mean(sampler.acceptance_fraction())
        return (sampler.get_chain(), sampler.get_lnprobability())

if __name__ == '__main__':
    lenspos = [0.5, 0.75]
    b = 1.3 # arcsec
    gamma = 0.03
    theta = 1.0 # rad
    sis = GravitationalLens(lenspos, b, gamma * np.cos(2. * theta), gamma * np.sin(2. * theta))
    imagepositions = sis.cross()
    posvar = 1.e-3
    print sis.ln_prior(imagepositions, None, posvar, None)
    imagefluxes = np.array([10., 10., 10., 10.])
    magtensors = sis.magnificationtensors(imagepositions)
    magscalars = np.array([np.linalg.det(ten) for ten in magtensors])
    chain, lnp = sis.sample_prior(imagepositions, None, posvar, None, 3000)
    plt.clf()
    for i in range(5):
        plt.plot(lnp[i,:])
    plt.xlabel('link number')
    plt.ylabel('ln prior probability')
    plt.savefig('lnp.png')
    for i in range(100):
        if i == 0:
            ipos = imagepositions
            print sis.ln_prior(imagepositions, None, posvar, None)
        else:
            ipos = np.reshape(chain[i,:,-1], (4,2))
        plt.clf()
        plt.subplot(111, aspect='equal')
        mags = np.abs(map(np.linalg.det, sis.magnificationtensors(ipos)))
        plt.scatter(ipos[:,0], ipos[:,1], s=mags, c='k', marker='o')
        plt.plot(lenspos[0], lenspos[1], 'kx')
        plt.xlim(-2, 3.)
        plt.ylim(-2, 3.)
        plt.xlabel('x (arcsec)')
        plt.ylabel('y (arcsec)')
        plt.savefig('sky-%02d.png' % i)
