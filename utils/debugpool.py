import multiprocessing as mp
import multiprocessing.queues
import multiprocessing.pool
import os

from astrometry.util.ttime import CpuMeas

#
# In Python 2.7 (and 2.6):
#
# Pool has an _inqueue (_quick_put) and _outqueue (_quick_get)
# and _taskqueue:
#
#
#   pool.map()  ---> sets cache[]
#               ---> put work on taskqueue
#       handle_tasks thread  ---> gets work from taskqueue
#                            ---> puts work onto inqueue
#       worker threads       ---> get work from inqueue
#                            ---> put results into outqueue
#       handle_results thread --> gets results from outqueue
#                             --> sets cache[]
#
#       meanwhile, handle_workers thread creates new workers as needed.
#
#
# map() etc add themselves to cache[jobid] = self
# and place work on the task queue.
#
# _handle_tasks pulls tasks from the _taskqueue and puts them
#     on the _inqueue.
#
# _handle_results pulls results from the _outqueue (job,i,obj)
#     and calls cache[job].set(i, obj)
#     (cache[job] is an ApplyResult / MapResult, etc.)
#
# worker threads run the worker() function:
#   run initializer
#   while true:
#     pull task from inqueue
#     job,i,func,arg,kwargs = task
#     put (job,i,result) on outqueue

# _inqueue,_outqueue are SimpleQueue (queues.py)
# get->recv=_reader.recv and put->send=_writer.send
# _reader,_writer = Pipe(duplex=False)

# Pipe (connection.py)
# uses os.pipe() with a _multiprocessing.Connection()
# on each fd.

# _multiprocessing = /u32/python/src/Modules/_multiprocessing/pipe_connection.c
# -> connection.h : send() is connection_send_obj()
# which uses pickle.
#
# Only _multiprocessing/socket_connection.c is used on non-Windows platforms.

import _multiprocessing
import cPickle as pickle
#import pickle
import time

class DebugConnection():
    def stats(self):
        return dict(pickle_objs = self.pobjs,
                    pickle_bytes = self.pbytes,
                    pickle_megabytes = 1e-6 * self.pbytes,
                    pickle_cputime = self.ptime,
                    unpickle_objs = self.upobjs,
                    unpickle_bytes = self.upbytes,
                    unpickle_megabytes = 1e-6 * self.upbytes,
                    unpickle_cputime = self.uptime)
    
    def __init__(self, fd, writable=True, readable=True):
        self.real = _multiprocessing.Connection(fd, writable=writable, readable=readable)
        self.ptime = 0.
        self.uptime = 0.
        self.pbytes = 0
        self.upbytes = 0
        self.pobjs = 0
        self.upobjs = 0
    def poll(self):
        return self.real.poll()
    def recv(self):
        bytes = self.real.recv_bytes()
        t0 = time.time()
        obj = pickle.loads(bytes)
        dt = time.time() - t0
        self.upbytes += len(bytes)
        self.uptime += dt
        self.upobjs += 1
        return obj

    def send(self, obj):
        t0 = time.time()
        s = pickle.dumps(obj, -1)
        dt = time.time() - t0
        self.pbytes += len(s)
        self.ptime += dt
        self.pobjs += 1
        return self.real.send_bytes(s)

    def close(self):
        return self.real.close()

def DebugPipe():
    fd1, fd2 = os.pipe()
    c1 = DebugConnection(fd1, writable=False)
    c2 = DebugConnection(fd2, readable=False)
    return c1,c2

from multiprocessing.queues import Lock

class DebugSimpleQueue(mp.queues.SimpleQueue):
    # new method
    def stats(self):
        S1 = self._reader.stats()
        S2 = self._writer.stats()
        return dict([(k, S1[k]+S2[k]) for k in S1.keys()])

    def __init__(self):
        (self._reader, self._writer) = DebugPipe()
        self._rlock = Lock()
        self._wlock = Lock()
        self._make_methods()
        # _make_methods creates two methods:
        #
        #  get:  self._rlock.acquire();
        #        self._reader.recv();
        #        self._rlock.release();
        #
        #  put:  self._wlock.acquire();
        #        self._write_send();
        #        self._wlock.release();
        #
        
import threading
import Queue
from multiprocessing import Process, cpu_count, TimeoutError
from multiprocessing.util import Finalize, debug
from multiprocessing.pool import RUN, CLOSE, TERMINATE

# def debug_handle_workers(pool):
#     while pool._worker_handler._state == RUN and pool._state == RUN:
#         print 'debug_handle_workers: pool state', pool._state
#         pool._maintain_pool()
#         time.sleep(0.5)
#     
#     print 'debug_handle_workers: pool state', pool._state, 'worker_handler state', pool._worker_handler._state
#     # send sentinel to stop workers
#     print 'Sending sentinel on task queue...'
#     pool._taskqueue.put(None)
#     # print 'Trying to join...'
#     for p in pool._pool:
#         print 'Worker', p
#         print 'isalive', p.is_alive()
#         if p.is_alive():
#             print 'terminating'
#             p.terminate()
#             print 'joining',
#             p.join(timeout=0.1)
#     print 'debug_handle_workers finishing.'
    

def debug_worker(inqueue, outqueue, progressqueue,
                 initializer=None, initargs=(),
                 maxtasks=None):
    #print 'debug_worker()'
    assert maxtasks is None or (type(maxtasks) == int and maxtasks > 0)
    put = outqueue.put
    get = inqueue.get
    if hasattr(inqueue, '_writer'):
        inqueue._writer.close()
        outqueue._reader.close()
        progressqueue._reader.close()
        
    if initializer is not None:
        initializer(*initargs)

    mypid = os.getpid()
        
    completed = 0
    while maxtasks is None or (maxtasks and completed < maxtasks):
        #t0 = time.time()
        try:
            print 'Worker pid', os.getpid(), 'getting task'
            task = get()
        except (EOFError, IOError):
            debug('worker got EOFError or IOError -- exiting')
            print 'Worker pid', os.getpid(), 'got EOF/IOErr getting task'
            break
        except KeyboardInterrupt as e:
            print 'debug_worker caught KeyboardInterrupt during get()'
            put((None, None, (None,(False,e))))
            raise SystemExit('ctrl-c')
            break

        if task is None:
            debug('worker got sentinel -- exiting')
            break

        job, i, func, args, kwds = task

        if progressqueue is not None:
            try:
                print 'Worker pid', os.getpid(), 'writing to progressqueue'
                progressqueue.put((job, i, mypid))
            except (EOFError, IOError):
                print 'worker got EOFError or IOError on progress queue -- exiting'
                break

        t1 = CpuMeas()
        try:
            success,val = (True, func(*args, **kwds))
        except Exception as e:
            success,val = (False, e)
            #print 'debug_worker: caught', e
        except KeyboardInterrupt as e:
            success,val = (False, e)
            #print 'debug_worker: caught ctrl-C during work', e
            #print type(e)
            put((None, None, (None,(False,e))))
            raise
        t2 = CpuMeas()
        dt = (t2.cpu_seconds_since(t1), t2.wall_seconds_since(t1))
        put((job, i, dt,(success,val)))
        completed += 1
    debug('worker exiting after %d tasks' % completed)

        
def debug_handle_results(outqueue, get, cache, beancounter, pool):
    thread = threading.current_thread()
    while 1:
        try:
            task = get()
        except (IOError, EOFError):
            debug('result handler got EOFError/IOError -- exiting')
            return
        if thread._state:
            assert thread._state == TERMINATE
            debug('result handler found thread._state=TERMINATE')
            break
        if task is None:
            debug('result handler got sentinel')
            break
        #print 'Got task:', task
        (job, i, dt, obj) = task
        # ctrl-C -> (None, None, None, (False, KeyboardInterrupt()))
        if job is None:
            (success, val) = obj
            if not success:
                if isinstance(val, KeyboardInterrupt):
                    #print 'Terminating due to KeyboardInterrupt'
                    thread._state = TERMINATE
                    pool._state = CLOSE
                    break
        try:
            #print 'cache[job]:', cache[job], 'job', job, 'i', i
            cache[job]._set(i, obj)
        except KeyError:
            pass
        beancounter.add_time(dt)

    while cache and thread._state != TERMINATE:
        try:
            task = get()
        except (IOError, EOFError):
            debug('result handler got EOFError/IOError -- exiting')
            return

        if task is None:
            debug('result handler ignoring extra sentinel')
            continue
        (job, i, dt, obj) = task
        if job is None:
            #print 'Ignoring another KeyboardInterrupt'
            continue
        try:
            cache[job]._set(i, obj)
        except KeyError:
            pass
        beancounter.add_time(dt)

    if hasattr(outqueue, '_reader'):
        debug('ensuring that outqueue is not full')
        # If we don't make room available in outqueue then
        # attempts to add the sentinel (None) to outqueue may
        # block.  There is guaranteed to be no more than 2 sentinels.
        try:
            for i in range(10):
                if not outqueue._reader.poll():
                    break
                get()
        except (IOError, EOFError):
            pass
    debug('result handler exiting: len(cache)=%s, thread._state=%s',
          len(cache), thread._state)

    #print 'debug_handle_results finishing.'


def debug_handle_tasks(taskqueue, put, outqueue, progressqueue, pool,
                       maxnqueued):
    thread = threading.current_thread()
    #print 'debug_handle_tasks starting'
    if progressqueue is not None and hasattr(progressqueue, '_writer'):
        progressqueue._writer.close()

    
    nqueued = 0
    
    for taskseq, set_length in iter(taskqueue.get, None):
        i = -1
        #print 'handle_tasks: task sequence', taskseq
        for i, task in enumerate(taskseq):
            print 'handle_tasks: got task', i
            if thread._state:
                debug('task handler found thread._state != RUN')
                break

            print 'N queue:', nqueued, 'max', maxnqueued
            try:
                print 'Queueing new task'
                put(task)
                nqueued += 1
            except IOError:
                debug('could not put task on queue')
                break

            print 'N queue:', nqueued, 'max', maxnqueued
            while maxnqueued and nqueued >= maxnqueued:
                try:
                    (job,i,pid) = progressqueue.get()
                    print 'Job', job, 'element', i, 'pid', pid, 'started'
                    nqueued -= 1
                except IOError:
                    break

        else:
            if set_length:
                debug('doing set_length()')
                set_length(i+1)
            continue
        break
    else:
        debug('task handler got sentinel')

    #print 'debug_handle_tasks got sentinel'

    try:
        # tell result handler to finish when cache is empty
        debug('task handler sending sentinel to result handler')
        outqueue.put(None)

        # tell workers there is no more work
        debug('task handler sending sentinel to workers')
        for p in pool:
            put(None)
    except IOError:
        debug('task handler got IOError when sending sentinels')

    #print 'debug_handle_tasks finishing'
    # 

    # Empty the progressqueue to prevent blocking writing workers?
    print 'task thread: emptying progressqueue'
    try:
        print 'task thread: reading from progressqueue.  nqueued=', nqueued
        (job,i,pid) = progressqueue.get()
        print 'Job', job, 'element', i, 'pid', pid, 'started'
        nqueued -= 1
    except IOError:
        pass
    print 'Task thread done.'
    

from multiprocessing.synchronize import Lock

class BeanCounter(object):
    def __init__(self):
        self.cpu = 0.
        self.wall = 0.
        self.lock = Lock()
    ### LOCKING
    def add_time(self, dt):
        self.lock.acquire()
        try:
            (cpu, wall) = dt
            self.cpu += cpu
            self.wall += wall
        finally:
            self.lock.release()
    def get_cpu(self):
        self.lock.acquire()
        try:
            return self.cpu
        finally:
            self.lock.release()
    def get_wall(self):
        self.lock.acquire()
        try:
            return self.wall
        finally:
            self.lock.release()
    def __str__(self):
        return 'CPU time: %.3fs s, Wall time: %.3fs' % (self.get_cpu(), self.get_wall())

class DebugPoolMeas(object):
    def __init__(self, pool):
        self.pool = pool
    def __call__(self):
        class FormatDiff(object):
            def __init__(self, pool):
                self.pool = pool
                self.t0 = self.now()
            def format_diff(self, other):
                t1 = self.t0
                t0 = other.t0
                return (('%.3f s worker CPU, %.3f s worker Wall, ' +
                         'pickled %i/%i objs, %.1f/%.1f MB') %
                        tuple(t1[k] - t0[k] for k in [
                    'worker_cpu', 'worker_wall', 'pickle_objs', 'unpickle_objs',
                    'pickle_megabytes', 'unpickle_megabytes']))
            def now(self):
                stats = self.pool.get_pickle_traffic()
                stats.update(worker_cpu = self.pool.get_worker_cpu(),
                             worker_wall = self.pool.get_worker_wall())
                return stats
        return FormatDiff(self.pool)

class DebugPool(mp.pool.Pool):
    def _setup_queues(self):
        self._inqueue = DebugSimpleQueue()
        self._outqueue = DebugSimpleQueue()
        self._quick_put = self._inqueue._writer.send
        self._quick_get = self._outqueue._reader.recv
        
    def get_pickle_traffic_string(self):
        S = self.get_pickle_traffic()
        return (('  pickled %i objs, %g MB, using %g s CPU\n' +
                 'unpickled %i objs, %g MB, using %g s CPU') %
                 (S[k] for k in [
                     'pickle_objs', 'pickle_megabytes', 'pickle_cputime',
                     'unpickle_objs', 'unpickle_megabytes', 'unpickle_cputime']))

    def get_pickle_traffic(self):
        S1 = self._inqueue.stats()
        S2 = self._outqueue.stats()
        return dict([(k, S1[k]+S2[k]) for k in S1.keys()])

    def get_worker_cpu(self):
        return self._beancounter.get_cpu()
    def get_worker_wall(self):
        return self._beancounter.get_wall()

    ### This just replaces the "worker" call with our "debug_worker".
    def _repopulate_pool(self):
        """Bring the number of pool processes up to the specified number,
        for use after reaping workers which have exited.
        """
        #print 'Repopulating pool with', (self._processes - len(self._pool)), 'workers'
        for i in range(self._processes - len(self._pool)):
            w = self.Process(target=debug_worker,
                             args=(self._inqueue, self._outqueue,
                                   self._progressqueue,
                                   self._initializer,
                                   self._initargs, self._maxtasksperchild)
                            )
            self._pool.append(w)
            w.name = w.name.replace('Process', 'PoolWorker')
            w.daemon = True
            w.start()
            debug('added worker')

#     def _join_exited_workers(self):
#         """Cleanup after any worker processes which have exited due to reaching
#         their specified lifetime.  Returns True if any workers were cleaned up.
#         """
#         cleaned = False
#         for i in reversed(range(len(self._pool))):
#             worker = self._pool[i]
#             print 'Worker', worker, 'exitcode', worker.exitcode
#             if worker.exitcode is not None:
#                 # worker exited
#                 debug('cleaning up worker %d' % i)
#                 worker.join()
#                 cleaned = True
#                 del self._pool[i]
#         return cleaned

    def map(self, func, iterable, chunksize=None):
        '''
        Equivalent of `map()` builtin
        '''
        assert self._state == RUN
        async = self.map_async(func, iterable, chunksize)
        while True:
            try:
                print 'Waiting for async result...'
                res = async.get(1)
                print 'Got async result'
                return res
            except multiprocessing.TimeoutError:
                print 'Timeout waiting for async result.'
                continue

    def map_async(self, func, iterable, chunksize=None, callback=None):
        '''
        Asynchronous equivalent of `map()` builtin
        '''
        assert self._state == RUN
        if not hasattr(iterable, '__len__'):
            iterable = list(iterable)

        if chunksize is None:
            chunksize, extra = divmod(len(iterable), len(self._pool) * 4)
            if extra:
                chunksize += 1
        if len(iterable) == 0:
            chunksize = 0

        result = mp.pool.MapResult(self._cache, chunksize, len(iterable), callback)
        mapstar = mp.pool.mapstar
        #print 'chunksize', chunksize
        #print 'Submitting job:', result._job
        #print 'Result:', result
        
        if chunksize == 1:
            self._taskqueue.put((((result._job, i, map, (func,(x,)), {})
                                  for i, x in enumerate(iterable)), None))

        else:
            task_batches = mp.pool.Pool._get_tasks(func, iterable, chunksize)
            self._taskqueue.put((((result._job, i, mapstar, (x,), {})
                                  for i, x in enumerate(task_batches)), None))
        return result
            
            

    
    # This is just copied from the superclass; we call our routines:
    #  -handle_results -> debug_handle_results
    # And add _beancounter.
    def __init__(self, processes=None, initializer=None, initargs=(),
                 maxtasksperchild=None, taskqueuesize=0):
        '''
        taskqueuesize: maximum number of tasks to put on the queue;
          this is actually done by keeping a progressqueue, written-to
          by workers as they take work off the inqueue, and read by
          the handle_tasks thread.  (Can't use a limit on _taskqueue,
          because (a) multi-element tasks are written; and (b)
          taskqueue is between the caller and the handle_tasks thread,
          which then just transfers the work to the inqueue, where it
          piles up.  Can't easily use a limit on inqueue because it is
          implemented via pipes with unknown, OS-controlled capacity
          in units of bytes.)
        '''
        self._beancounter = BeanCounter()
        self._setup_queues()
        self._taskqueue = Queue.Queue()
        self._cache = {}
        self._state = RUN
        self._initializer = initializer
        self._initargs = initargs
        self._maxtasksperchild = maxtasksperchild

        if taskqueuesize:
            self._progressqueue = DebugSimpleQueue()
        else:
            self._progressqueue = None
        
        if processes is None:
            try:
                processes = cpu_count()
            except NotImplementedError:
                processes = 1

        if initializer is not None and not hasattr(initializer, '__call__'):
            raise TypeError('initializer must be a callable')

        self._processes = processes
        self._pool = []
        self._repopulate_pool()

        self._worker_handler = threading.Thread(
        target=mp.pool.Pool._handle_workers,
        #target = debug_handle_workers,
        args=(self, )
            )
        self._worker_handler.name = 'WorkerHandler'
        self._worker_handler.daemon = True
        self._worker_handler._state = RUN
        self._worker_handler.start()

        if True:
            self._task_handler = threading.Thread(
                target=debug_handle_tasks,
                args=(self._taskqueue, self._quick_put, self._outqueue,
                      self._progressqueue, self._pool,
                      taskqueuesize))
        else:
            self._task_handler = threading.Thread(
                target=mp.pool.Pool._handle_tasks,
                args=(self._taskqueue, self._quick_put, self._outqueue,
                      self._pool))
              
        self._task_handler.name = 'TaskHandler'
        self._task_handler.daemon = True
        self._task_handler._state = RUN
        self._task_handler.start()

        self._result_handler = threading.Thread(
            target=debug_handle_results,
            args=(self._outqueue, self._quick_get, self._cache,
                  self._beancounter, self)
            )
        self._result_handler.name = 'ResultHandler'
        self._result_handler.daemon = True
        self._result_handler._state = RUN
        self._result_handler.start()

        self._terminate = Finalize(
            self, self._terminate_pool,
            args=(self._taskqueue, self._inqueue, self._outqueue, self._pool,
                  self._worker_handler, self._task_handler,
                  self._result_handler, self._cache),
            exitpriority=15
            )


class iterwrapper(object):
    def __init__(self, y, n):
        self.n = n
        self.y = y
    def __str__(self):
        return 'iterwrapper: n=%i; ' % self.n + self.y
    def __iter__(self):
        return self
    def next(self):
        return self.y.next()
    def __len__(self):
        return self.n
    

if __name__ == '__main__':

    import sys
    from astrometry.util import multiproc
    from astrometry.util.ttime import *

    # import logging
    # lvl = logging.DEBUG
    # logging.basicConfig(level=lvl, format='%(message)s', stream=sys.stdout)
    # import multiprocessing
    # multiprocessing.get_logger()
    
    def work((i)):
        print 'Doing work', i
        time.sleep(2)
        print 'Done work', i
        return i
        
    class ywrapper(object):
        def __init__(self, y, n):
            self.n = n
            self.y = y
        def __str__(self):
            return 'ywrapper: n=%i; ' % self.n + self.y
        def __iter__(self):
            return self
        def next(self):
            return self.y.next()
        def __len__(self):
            return self.n

    def yielder(n):
        for i in range(n):
            print 'Yielding', i
            yield i

    N = 20
    y = yielder(N)
    args = ywrapper(y, N)
    
    dpool = DebugPool(4, taskqueuesize=4)
    dmup = multiproc.multiproc(pool=dpool)
    Time.add_measurement(DebugPoolMeas(dpool))

    t0 = Time()
    res = dmup.map(work, args)
    print Time()-t0
    print 'Got result:', res
    
    sys.exit(0)



    from tractor import *
    from tractor import sdss as st
    
    from tractor.engine import getmodelimagefunc2
    
    class Tractor2(Tractor):
        def _map(self, *args):
            t0 = Time()
            R = super(Tractor2,self)._map(*args)
            print 'map:', Time()-t0
            return R
    
        def getderivs2(self):
            alldata = []
            for im in self.images:
                alldata.append((im.data,im.invvar, im.inverr,im.origInvvar))
                im.shape = im.data.shape
                im.data,im.invvar = None,None
                im.inverr,im.origInvvar = None,None
                #print 'Image:', dir(im)
            R = super(Tractor2,self).getderivs2()
            for im,d in zip(self.images, alldata):
                im.data,im.invvar, im.inverr, im.origInvvar = d
            return R
    
        def getModelImages(self):
            if self.is_multiproc():
                # avoid shipping my images...
                allimages = self.getImages()
                self.images = []
    
                alldata = []
                for im in allimages:
                    alldata.append((im.data,im.invvar, im.inverr,im.origInvvar))
                    im.shape = im.data.shape
                    im.data,im.invvar = None,None
                    im.inverr,im.origInvvar = None,None
    
                mods = self._map(getmodelimagefunc2, [(self, im) for im in allimages])
    
                for im,d in zip(allimages, alldata):
                    im.data,im.invvar, im.inverr, im.origInvvar = d
    
                self.images = allimages
            else:
                mods = [self.getModelImage(img) for img in self.images]
            return mods
    
    
    
        # def getModelPatchNoCache(self, img, src):
        #   data,invvar = img.data,img.invvar
        #   img.shape = data.shape
        #   del img.data
        #   del img.invvar
        #   R = super(Tractor2,self).getModelPatchNoCache(img, src)
        #   img.data, img.invvar = data,invvar
    
    

    #run,camcol,field = 7164,4,273
    #band='g'
    run,camcol,field = 2662, 4, 111
    band='i'
    roi=[0,300,0,300]
    im,info = st.get_tractor_image(run, camcol, field, band,
                                   useMags=True, roi=roi)
    sources = st.get_tractor_sources(run, camcol, field, band, roi=roi)
    tractor = Tractor2([im], sources)
    print tractor
    print tractor.getLogProb()
    tractor.freezeParam('images')

    p0 = tractor.getParams()
    tractor.setParams(p0)

    print
    print 'With Debug:'
    tractor.setParams(p0)
    tractor.mp = dmup
    t0 = Time()
    tractor.opt2()
    print 'With Debug:', Time()-t0
    print dpool.get_pickle_traffic_string()
    print dpool.get_worker_cpu(), 'worker CPU'
    print Time()-t0
    sys.exit(0)

    pool = mp.Pool(4)
    mup = multiproc.multiproc(pool=pool)

    for i in range(3):
        print
        print 'With Debug:'
        tractor.setParams(p0)
        tractor.mp = dmup
        t0 = Time()
        tractor.opt2()
        print 'With Debug:', Time()-t0

        print
        print 'With vanilla:'
        tractor.setParams(p0)
        tractor.mp = mup
        t0 = Time()
        tractor.opt2()
        print 'With vanilla:', Time()-t0



