from distutils.core import setup, Extension
from numpy.distutils.misc_util import get_numpy_include_dirs

numpy_inc = get_numpy_include_dirs()

#sources = ['mix_wrap.c' ],
c_swig_module = Extension('_mix',
                          sources = ['mix.i'],
                          include_dirs = numpy_inc,
						  extra_objects = [],
    )
#undef_macros=['NDEBUG'],
#extra_compile_args=['-O0','-g'],
#extra_link_args=['-O0', '-g'],


setup(name = 'Gaussian mixtures',
	  version = '1.0',
	  description = '',
	  author = 'Lang & Hogg',
	  author_email = 'dstn@astro.princeton.edu',
	  url = 'http://astrometry.net',
	  py_modules = [],
	  ext_modules = [c_swig_module])

