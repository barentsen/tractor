"""
This code is part of the Tractor.

Copyright 2012 David W. Hogg

This code builds a simultaneous emitting dust model for a set of
aligned Herschel maps.

It has various things related to Herschel data hard-coded.
"""

import matplotlib
matplotlib.use('Agg')
import pylab as plt

import numpy as np
from tractor import *

from tractor.cache import Cache
import pyfits
from astrometry.util.util import Tan, tan_wcs_resample, log_init
from astrometry.util.multiproc import multiproc
from astrometry.util.file import pickle_to_file, unpickle_from_file
import multiprocessing

class Physics(object):
	# all the following from physics.nist.gov
	# http://physics.nist.gov/cuu/Constants/Table/allascii.txt
	hh = 6.62606957e-34 # J s
	cc = 299792458. # m s^{-1}
	kk = 1.3806488e-23 # J K^{-1}

	@staticmethod
	def black_body(lam, lnT):
		"""
		Compute the black-body formula, for a given lnT.

		'lam' is wavelength in meters
		'lnT' is log-temperature in Kelvin

		Return value is in [J s^-1 m^-3],
		power radiated per square meter (area), per meter of wavelength
		"""
		return (2. * Physics.hh * (Physics.cc ** 2) * (lam ** -5) /
				(np.exp(Physics.hh * Physics.cc / (lam * Physics.kk * np.exp(lnT))) - 1.))

class DustPhotoCal(ParamList):
	def __init__(self, lam, pixscale):   #, Mjypersrperdn):
		'''
		lam: central wavelength of filter in microns

		Mjypersrperdn: photometric calibration in mega-Jansky per
		steradian per DN.

		Jansky: 1e-26 watts per square meter per hertz.

		self.cal is the calibration factor that scales SI units to
		image pixel values.  The images are in MJy/Sr, so

		ie, [Joules/s/m^2/m] * [self.cal] = [MJy/Sr]

		So self.cal has to be in units of [m s / Sr], which we get from lam^2/c
		and the pixel scale (?)

		'''
		self.lam = lam
		self.cal = 1e20 * lam**2 / Physics.cc
		self.cal /= ((pixscale / 3600 / (180./np.pi))**2)
		print 'Cal', self.cal
		# No (adjustable) params
		super(DustPhotoCal,self).__init__()


	# MAGIC number: wavelength scale for emissivity model
	lam0 = 1.e-4 # m

	def brightnessToCounts(self, brightness):
		# see http://arxiv.org/abs/astro-ph/9902255 for (lam/lam0) ^ -beta, eg
		beta = np.array(brightness.emissivity)
		print 'beta', beta.shape, 'range', beta.min(), beta.max()
		return(
			np.exp(brightness.logsolidangle)
			* ((self.lam / DustPhotoCal.lam0) ** (-1. * beta))
			* Physics.black_body(self.lam, brightness.logtemperature)
			* self.cal)

class NpArrayParams(ParamList):
	'''
	An implementation of Params that holds values in an np.ndarray
	'''
	def __init__(self, a):
		self.a = np.array(a)
		super(NpArrayParams, self).__init__()
		del self.vals
		# from NamedParams...
		# active/inactive
		self.liquid = [True] * self._numberOfThings()

	def __getattr__(self, name):
		if name == 'vals':
			return self.a.ravel()
		if name in ['shape',]:
			return getattr(self.a, name)
		raise AttributeError() #name + ': no such attribute in NpArrayParams.__getattr__')

	def __getstate__(self): return self.__dict__
	def __setstate__(self, d): self.__dict__.update(d)


# def _map_dsderivs((ds, i, pnew, img)):
# 	print 'Img', img.name, 'deriv', i
# 	oldval = ds.setParam(i, p0[i] + step)
# 	modi = ds.getModelPatch(img)
# 	ds.setParam(i, oldval)
# 	return modi
	

class DustSheet(MultiParams):
	'''
	A Source that represents a smooth sheet of dust.  The dust parameters are help in arrays
	that represent sample points on the sky; they are interpolated onto the destination image.
	'''
	@staticmethod
	def getNamedParams():
		return dict(logsolidangle=0, logtemperature=1, emissivity=2)

	def __init__(self, logsolidangle, logtemperature, emissivity, wcs):
		assert(logsolidangle.shape == logtemperature.shape)
		assert(logsolidangle.shape == emissivity.shape)
		assert(logsolidangle.shape == (wcs.get_height(), wcs.get_width()))
		super(DustSheet, self).__init__(NpArrayParams(logsolidangle),
										NpArrayParams(logtemperature),
										NpArrayParams(emissivity))
		self.wcs = wcs

		self.Tcache = {}

	def getArrays(self):
		shape = self.shape
		logsa = self.logsolidangle.a.reshape(shape)
		logt  = self.logtemperature.a.reshape(shape)
		emis  = self.emissivity.a.reshape(shape)
		return (logsa, logt, emis)


	def __getattr__(self, name):
		if name == 'shape':
			return self.logsolidangle.shape
		raise AttributeError() #name + ': no such attribute in DustSheet.__getattr__')

	def __getstate__(self): #return self.__dict__
		D = self.__dict__.copy()
		D.pop('mp', None)
		return D
	def __setstate__(self, d): self.__dict__.update(d)

	def _getcounts(self, img):
		# This takes advantage of the coincidence that our DustPhotoCal does the right thing
		# with numpy arrays.

		#class fakebright(object):
		#	pass
		#b = fakebright()
		#b.logsolidangle, b.logtemperature, b.emissivity = self.getArrays()
		#counts = img.getPhotoCal().brightnessToCounts(b)

		counts = img.getPhotoCal().brightnessToCounts(self)
		# Thanks to NpArrayParams they get ravel()'d down to 1-d, so
		# reshape back to 2d
		counts = counts.reshape(self.shape) #.astype(np.float32)
		return counts

	def _computeTransformation(self, img):
		imwcs = img.getWcs()
		# Brutal pre-computation of the transformation matrix...
		H,W = self.shape
		Ngrid = W*H
		iH,iW = img.shape
		Nim = iW*iH
		cmock = np.zeros((H,W), np.float32)
		rim = np.zeros((iH,iW), np.float32)
		X = np.zeros((Nim, Ngrid), np.float32)
		for i in range(H):
			print 'Precomputing matrix for image', img.name, 'row', i
			for j in range(W):
				#print 'Precomputing matrix for grid pixel', j,i
				cmock[i,j] = 1.
				res = tan_wcs_resample(self.wcs, imwcs.wcs, cmock, rim, 2)
				assert(res == 0)
				outimg = img.getPsf().applyTo(rim)
				X[:, i*W+j] = outimg.ravel()
				cmock[i,j] = 0.
		return X

	def _setTransformation(self, img, X):
		key = (img.getWcs(), img.getPsf())
		self.Tcache[key] = X

	def _getTransformation(self, img):
		imwcs = img.getWcs()
		key = (imwcs,img.getPsf())
		if not key in self.Tcache:
			X = self._computeTransformation(img)
			self.Tcache[key] = X
		else:
			X = self.Tcache[key]
		return X

	def getModelPatch(self, img):
		X = self._getTransformation(img)

		imwcs = img.getWcs()
		counts = self._getcounts(img)
		#print 'Img shape', img.shape
		#print 'counts shape', counts.shape
		#print 'X shape', X.shape
		#rim = np.dot(X, counts.ravel())
		#print 'dot shape', rim.shape
		#rim = rim.reshape(img.shape)
		rim = np.dot(X, counts.ravel()).reshape(img.shape)

		gridscale = self.wcs.pixel_scale()
		imscale = imwcs.wcs.pixel_scale()
		#print 'pixel scaling:', (imscale / gridscale)**2
		rim *= (imscale / gridscale)**2
		#print 'Median model patch:', np.median(rim)
		return Patch(0, 0, rim)

	def getModelPatch_1(self, img):
		# Compute emission in the native grid
		# Resample onto img grid + do PSF convolution in one shot

		# I am weak... do resampling + convolution in separate shots
		counts = self._getcounts(img)
		iH,iW = img.shape
		rim = np.zeros((iH,iW), np.float32)
		imwcs = img.getWcs()

		## ASSUME TAN WCS on both the DustSheet and img.
		res = tan_wcs_resample(self.wcs, imwcs.wcs, counts, rim, 2)
		assert(res == 0)

		# Correct for the difference in solid angle per pixel of the grid and the image.
		# NB we should perhaps do this before the call to "brightnessToCounts", but it's a
		# linear scale factor so it doesn't really matter.
		gridscale = self.wcs.pixel_scale()
		imscale = imwcs.wcs.pixel_scale()
		rim *= (imscale / gridscale)**2

		## ASSUME the PSF has "applyTo"
		outimg = img.getPsf().applyTo(rim)

		return Patch(0, 0, outimg)

	def getStepSizes(self, *args, **kwargs):
		return ([0.1] * len(self.logsolidangle) +
				[0.1] * len(self.logtemperature) +
				[0.1] * len(self.emissivity))


	def getParamDerivatives(self, img):
		X = self._getTransformation(img)

		imwcs = img.getWcs()
		gridscale = self.wcs.pixel_scale()
		imscale = imwcs.wcs.pixel_scale()
		cscale = (imscale / gridscale)**2

		imshape = img.shape

		p0 = self.getParams()
		counts0 = self._getcounts(img)
		derivs = []

		# This is ugly -- the dust param vectors are stored one after another, so the
		# three parameters affecting each pixel are not contiguous, and we also ignore the
		# fact that we should *know* which grid pixel is affected by each parameter!!
		# (but this might work with freezing... maybe...)

		#for i,step in enumerate(self.getStepSizes()):
		for i,(step,name) in enumerate(zip(self.getStepSizes(), self.getParamNames())):
			print 'Img', img.name, 'deriv', i, name
			oldval = self.setParam(i, p0[i] + step)
			countsi = self._getcounts(img)
			pnow = self.getParams()
			dp = (np.array(pnow) - np.array(p0))
			self.setParam(i, oldval)
			print 'dparams', dp

			dc = (countsi - counts0).ravel()
			I = np.flatnonzero(dc != 0)
			if len(I) != 1:
				print 'I', I
				print 'i', i
				print 'step', step
				print 'oldval', oldval
				print 'p0[i]', p0[i]
				print '+step', p0[i]+step
				print 'countsi', countsi
				print 'counts0', counts0
				print 'dcounts', countsi - counts0
				print 'rav', (countsi - counts0).ravel()
			if len(I) == 0:
				derivs.append(None)
				continue
			#assert(len(I) == 1)
			ii = I[0]

			#xx = X[:,ii]
			#print 'mean non-zero X elements:', np.mean(xx[np.flatnonzero(xx)])
			#print 'dc', dc[ii]
			#print 'scale', cscale / step

			dmod = ((X[:,ii] * dc[ii]) * (cscale / step)).reshape(imshape)
			derivs.append(Patch(0, 0, dmod))


		return derivs


	def getParamDerivatives_1(self, img):
		# Super-naive!!
		p0 = self.getParams()
		mod0 = self.getModelPatch(img)
		#counts0 = self._getcounts(img)
		derivs = []

		# This doesn't work because the tractor already map()s this function
		# ss = self.getStepSizes()
		# if hasattr(self, 'mp'):
		# 	modis = self.mp.map(_map_dsderivs, [(i,step,img) for i,step in enumerate(ss)])
		# 	for modi,step in sip(modis,ss):
		# 		d = (modi - mod0) * (1./step)
		# 		derivs.append(d)
		# 	return derivs

		for i,step in enumerate(self.getStepSizes()):
			print 'Img', img.name, 'deriv', i
			oldval = self.setParam(i, p0[i] + step)

			# countsi = self._getcounts(img)
			# I = (countsi != counts0)
			# xi = np.where(np.sum(I, axis=0) > 0)
			# yi = np.where(np.sum(I, axis=1) > 0)
			# xi = min(xi),max(xi)
			# yi = min(yi),max(yi)
			# Expand by Lanczos + PSF kernel

			modi = self.getModelPatch(img)
			self.setParam(i, oldval)
			d = (modi - mod0) * (1./step)
			derivs.append(d)
		return derivs

def makeplots(tractor, step, suffix):
	print 'Rendering synthetic images...'
	mods = tractor.getModelImages()
	for i,mod in enumerate(mods):
		tim = tractor.getImage(i)
		ima = dict(interpolation='nearest', origin='lower',
				   vmin=tim.zr[0], vmax=tim.zr[1])
		plt.clf()
		plt.imshow(mod, **ima)
		plt.gray()
		plt.colorbar()
		plt.title(tim.name)
		plt.savefig('model-%i-%02i%s.png' % (i, step, suffix))

		if step == 0:
			plt.clf()
			plt.imshow(tim.getImage(), **ima)
			plt.gray()
			plt.colorbar()
			plt.title(tim.name)
			plt.savefig('data-%i.png' % (i))

		plt.clf()
		# tractor.getChiImage(i), 
		plt.imshow((tim.getImage() - mod) * tim.getInvError(),
				   interpolation='nearest', origin='lower',
				   vmin=-5, vmax=+5)
		plt.gray()
		plt.colorbar()
		plt.title(tim.name)
		plt.savefig('chi-%i-%02i%s.png' % (i, step, suffix))

	ds = tractor.getCatalog()[0]
	print 'Dust sheet:', ds
	logsa, logt, emis = ds.getArrays()
	plt.clf()
	plt.imshow(logsa, interpolation='nearest', origin='lower')
	plt.gray()
	plt.colorbar()
	plt.title('Dust: log(solid angle)')
	plt.savefig('logsa-%02i%s.png' % (step, suffix))

	plt.clf()
	plt.imshow(logt, interpolation='nearest', origin='lower')
	plt.gray()
	plt.colorbar()
	plt.title('Dust: log(temperature)')
	plt.savefig('logt-%02i%s.png' % (step, suffix))

	plt.clf()
	plt.imshow(emis, interpolation='nearest', origin='lower')
	plt.gray()
	plt.colorbar()
	plt.title('Dust: emissivity')
	plt.savefig('emis-%02i%s.png' % (step, suffix))

	print 'Finished plotting images'

def main():
	import optparse
	import logging
	import sys

	###
	log_init(3)
	
	parser = optparse.OptionParser()
	parser.add_option('--threads', dest='threads', default=1, type=int, help='Use this many concurrent processors')
	parser.add_option('-v', '--verbose', dest='verbose', action='count', default=0,
					  help='Make more verbose')

	parser.add_option('--grid', '-g', dest='gridn', type=int, default=5, help='Dust parameter grid size')
	parser.add_option('--steps', '-s', dest='steps', type=int, default=10, help='# Optimization step')
	parser.add_option('--suffix', dest='suffix', default='', help='Output file suffix')

	parser.add_option('--no-100', dest='no100', action='store_true', default=False,
					  help='Omit PACS-100 data?')

	parser.add_option('--callgrind', dest='callgrind', action='store_true', default=False, help='Turn on callgrind around tractor.optimize()')

	opt,args = parser.parse_args()

	if opt.verbose == 0:
		lvl = logging.INFO
	else:
		lvl = logging.DEBUG
	logging.basicConfig(level=lvl, format='%(message)s', stream=sys.stdout)

	if opt.threads > 1 and False:
		global dpool
		import debugpool
		dpool = debugpool.DebugPool(opt.threads)
		Time.add_measurement(debugpool.DebugPoolMeas(dpool))
		mp = multiproc(pool=dpool)
	else:
		mp = multiproc(opt.threads)#, wrap_all=True)

	if opt.callgrind:
		import callgrind
	else:
		callgrind = None


	"""
	Brittle function to read Groves data sample and make the
	things we need for fitting.
	
	# issues:
	- Need to read the WCS too.
	- Need, for each image, to construct the rectangular
	nearest-neighbor interpolation indices to image 0.
	- Can I just blow up the SPIRE images with interpolation?
	"""
	dataList = [
		('m31_brick15_PACS100.fits',   7.23,  7.7),
		('m31_brick15_PACS160.fits',   3.71, 12.0),
		('m31_brick15_SPIRE250.fits',  None, 18.0),
		('m31_brick15_SPIRE350.fits',  None, 25.0),
		('m31_brick15_SPIRE500.fits',  None, 37.0),
		]

	if opt.no100:
		dataList = dataList[1:]

	print 'Reading images...'
	tims = []
	for i, (fn, noise, fwhm) in enumerate(dataList):
		print
		print 'Reading', fn
		P = pyfits.open(fn)
		image = P[0].data.astype(np.float32)
		#wcs = Tan(fn, 0)
		H,W = image.shape
		hdr = P[0].header
		wcs = Tan(hdr['CRVAL1'], hdr['CRVAL2'],
				  hdr['CRPIX1'], hdr['CRPIX2'],
				  hdr['CDELT1'], 0., 0., hdr['CDELT2'], W, H)
		assert(hdr['CROTA2'] == 0.)
		if noise is None:
			noise = float(hdr['NOISE'])
		print 'Noise', noise
		print 'Median image value', np.median(image)
		invvar = np.ones_like(image) / (noise**2)

		skyval = np.percentile(image, 5)
		sky = ConstantSky(skyval)

		lam = float(hdr['FILTER'])
		print 'Lambda', lam
		# calibrated, yo
		assert(hdr['BUNIT'] == 'MJy/Sr')
		# "Filter" is in *microns* in the headers; convert to *m* here, to match "lam0"
		pcal = DustPhotoCal(lam * 1e-6, wcs.pixel_scale())   #, 1.)
		#nm = '%s %i' % (hdr['INSTRUME'], lam)
		nm = fn.replace('.fits', '')
		#zr = noise * np.array([-3, 10]) + skyval
		zr = np.array([np.percentile(image.ravel(), p) for p in [1, 99]])
		print 'Pixel scale:', wcs.pixel_scale()
		# meh
		sigma = fwhm / wcs.pixel_scale() / 2.35
		print 'PSF sigma', sigma, 'pixels'
		psf = NCircularGaussianPSF([sigma], [1.])
		twcs = FitsWcs(wcs)
		tim = Image(data=image, invvar=invvar, psf=psf, wcs=twcs,
					sky=sky, photocal=pcal, name=nm)
		tim.zr = zr
		tims.append(tim)

		# plt.clf()
		# plt.hist(image.ravel(), 100)
		# plt.title(nm)
		# plt.savefig('im%i-hist.png' % i)

	plt.clf()
	for tim,c in zip(tims, ['b','g','y',(1,0.5,0),'r']):
		H,W = tim.shape
		twcs = tim.getWcs()
		rds = []
		for x,y in [(0.5,0.5),(W+0.5,0.5),(W+0.5,H+0.5),(0.5,H+0.5),(0.5,0.5)]:
			rd = twcs.pixelToPosition(x,y)
			rds.append(rd)
		rds = np.array(rds)
		plt.plot(rds[:,0], rds[:,1], '-', color=c, lw=2, alpha=0.5)
	plt.savefig('radec.png')

	print 'Creating dust sheet...'
	N = opt.gridn

	# Build a WCS for the dust sheet to match the first image (assuming it's square and axis-aligned)
	
	wcs = tims[0].getWcs().wcs
	r,d = wcs.radec_center()
	H,W = tims[0].shape
	scale = wcs.pixel_scale()
	scale *= float(W)/N / 3600.
	c = float(N)/2. + 0.5
	dwcs = Tan(r, d, c, c, scale, 0, 0, scale, N, N)

	rds = []
	H,W = N,N
	for x,y in [(0.5,0.5),(W+0.5,0.5),(W+0.5,H+0.5),(0.5,H+0.5),(0.5,0.5)]:
		r,d = dwcs.pixelxy2radec(x,y)
		rds.append((r,d))
	rds = np.array(rds)
	plt.plot(rds[:,0], rds[:,1], 'k-', lw=1, alpha=1)
	plt.savefig('radec2.png')

	pixscale = dwcs.pixel_scale()
	logsa = np.log(1e-3 * ((pixscale / 3600 / (180./np.pi))**2))

	logsa = np.zeros((H,W)) + logsa
	logt = np.zeros((H,W)) + np.log(17.)
	emis = np.zeros((H,W)) + 2.

	ds = DustSheet(logsa, logt, emis, dwcs)
	print 'DustSheet:', ds
	#print 'np', ds.numberOfParams()
	#print 'pn', ds.getParamNames()
	#print 'p', ds.getParams()

	cat = Catalog()
	cat.append(ds)
	
	tractor = Tractor(Images(*tims), cat)
	tractor.mp = mp
	## hack
	#ds.mp = mp

	print 'Precomputing transformations...'
	XX = mp.map(_map_trans, [(ds,im) for im in tractor.getImages()])
	for im,X in zip(tractor.getImages(), XX):
		ds._setTransformation(im, X)
	print 'done precomputing.'

	makeplots(tractor, 0, opt.suffix)

	for im in tractor.getImages():
		im.freezeAllBut('sky')

	for i in range(opt.steps):
		#tractor.optimize(damp=10.)

		if callgrind:
			callgrind.callgrind_start_instrumentation()

		tractor.optimize(damp=1., alphas=[1e-3, 1e-2, 0.1, 0.3, 1., 3., 10., 30., 100.])

		if callgrind:
			callgrind.callgrind_stop_instrumentation()

		makeplots(tractor, 1 + i, opt.suffix)
		pickle_to_file(tractor, 'herschel-%02i%s.pickle' % (i, opt.suffix))


def _map_trans((ds, img)):
	return ds._computeTransformation(img)

if __name__ == '__main__':
	main()
	#import cProfile
	#import sys
	#from datetime import tzinfo, timedelta, datetime
	#cProfile.run('main()', 'prof-%s.dat' % (datetime.now().isoformat()))
	#sys.exit(0)
