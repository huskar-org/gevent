from __future__ import with_statement
from time import time
import gevent
from gevent import pool
from gevent.event import Event
from gevent.queue import Queue
import greentest
import random
from greentest import ExpectedException
import six

import unittest


class TestCoroutinePool(unittest.TestCase):
    klass = pool.Pool

    def test_apply_async(self):
        done = Event()

        def some_work(x):
            done.set()

        pool = self.klass(2)
        pool.apply_async(some_work, ('x', ))
        done.wait()

    def test_apply(self):
        value = 'return value'

        def some_work():
            return value

        pool = self.klass(2)
        result = pool.apply(some_work)
        self.assertEqual(value, result)

    def test_multiple_coros(self):
        evt = Event()
        results = []

        def producer():
            gevent.sleep(0.001)
            results.append('prod')
            evt.set()

        def consumer():
            results.append('cons1')
            evt.wait()
            results.append('cons2')

        pool = self.klass(2)
        done = pool.spawn(consumer)
        pool.apply_async(producer)
        done.get()
        self.assertEqual(['cons1', 'prod', 'cons2'], results)

    def dont_test_timer_cancel(self):
        timer_fired = []

        def fire_timer():
            timer_fired.append(True)

        def some_work():
            gevent.timer(0, fire_timer)

        pool = self.klass(2)
        pool.apply(some_work)
        gevent.sleep(0)
        self.assertEqual(timer_fired, [])

    def test_reentrant(self):
        pool = self.klass(1)
        result = pool.apply(pool.apply, (lambda a: a + 1, (5, )))
        self.assertEqual(result, 6)
        evt = Event()
        pool.apply_async(evt.set)
        evt.wait()

    def test_stderr_raising(self):
        # testing that really egregious errors in the error handling code
        # (that prints tracebacks to stderr) don't cause the pool to lose
        # any members
        import sys
        pool = self.klass(size=1)

        # we're going to do this by causing the traceback.print_exc in
        # safe_apply to raise an exception and thus exit _main_loop
        normal_err = sys.stderr
        try:
            sys.stderr = FakeFile()
            waiter = pool.spawn(crash)
            with gevent.Timeout(2):
                self.assertRaises(RuntimeError, waiter.get)
            # the pool should have something free at this point since the
            # waiter returned
            # pool.Pool change: if an exception is raised during execution of a link,
            # the rest of the links are scheduled to be executed on the next hub iteration
            # this introduces a delay in updating pool.sem which makes pool.free_count() report 0
            # therefore, sleep:
            gevent.sleep(0)
            self.assertEqual(pool.free_count(), 1)
            # shouldn't block when trying to get
            t = gevent.Timeout.start_new(0.1)
            try:
                pool.apply(gevent.sleep, (0, ))
            finally:
                t.cancel()
        finally:
            sys.stderr = normal_err
            pool.join()


def crash(*args, **kw):
    raise RuntimeError("Whoa")


class FakeFile(object):

    def write(*args):
        raise RuntimeError('Whaaa')


class PoolBasicTests(greentest.TestCase):
    klass = pool.Pool

    def test_execute_async(self):
        p = self.klass(size=2)
        self.assertEqual(p.free_count(), 2)
        r = []

        first = p.spawn(r.append, 1)
        self.assertEqual(p.free_count(), 1)
        first.get()
        self.assertEqual(r, [1])
        gevent.sleep(0)
        self.assertEqual(p.free_count(), 2)

        #Once the pool is exhausted, calling an execute forces a yield.

        p.apply_async(r.append, (2, ))
        self.assertEqual(1, p.free_count())
        self.assertEqual(r, [1])

        p.apply_async(r.append, (3, ))
        self.assertEqual(0, p.free_count())
        self.assertEqual(r, [1])

        p.apply_async(r.append, (4, ))
        self.assertEqual(r, [1])
        gevent.sleep(0.01)
        self.assertEqual(sorted(r), [1, 2, 3, 4])

    def test_discard(self):
        p = self.klass(size=1)
        first = p.spawn(gevent.sleep, 1000)
        p.discard(first)
        first.kill()
        assert not first, first
        self.assertEqual(len(p), 0)
        self.assertEqual(p._semaphore.counter, 1)

    def test_add_method(self):
        p = self.klass(size=1)
        first = gevent.spawn(gevent.sleep, 1000)
        try:
            second = gevent.spawn(gevent.sleep, 1000)
            try:
                self.assertEqual(p.free_count(), 1)
                self.assertEqual(len(p), 0)
                p.add(first)
                timeout = gevent.Timeout(0.1)
                timeout.start()
                try:
                    p.add(second)
                except gevent.Timeout:
                    pass
                else:
                    raise AssertionError('Expected timeout')
                finally:
                    timeout.cancel()
                self.assertEqual(p.free_count(), 0)
                self.assertEqual(len(p), 1)
            finally:
                second.kill()
        finally:
            first.kill()

    def test_apply(self):
        p = self.klass()
        result = p.apply(lambda a: ('foo', a), (1, ))
        self.assertEqual(result, ('foo', 1))

    def test_init_error(self):
        self.switch_expected = False
        self.assertRaises(ValueError, self.klass, -1)

#
# tests from standard library test/test_multiprocessing.py


class TimingWrapper(object):

    def __init__(self, func):
        self.func = func
        self.elapsed = None

    def __call__(self, *args, **kwds):
        t = time()
        try:
            return self.func(*args, **kwds)
        finally:
            self.elapsed = time() - t


def sqr(x, wait=0.0):
    gevent.sleep(wait)
    return x * x


def squared(x):
    return x * x


def sqr_random_sleep(x):
    gevent.sleep(random.random() * 0.1)
    return x * x


def final_sleep():
    for i in range(3):
        yield i
    gevent.sleep(0.2)


TIMEOUT1, TIMEOUT2, TIMEOUT3 = 0.082, 0.035, 0.14


class TestPool(greentest.TestCase):
    __timeout__ = 5
    size = 1

    def setUp(self):
        greentest.TestCase.setUp(self)
        self.pool = pool.Pool(self.size)

    def cleanup(self):
        self.pool.join()

    def test_apply(self):
        papply = self.pool.apply
        self.assertEqual(papply(sqr, (5,)), 25)
        self.assertEqual(papply(sqr, (), {'x': 3}), 9)

    def test_map(self):
        pmap = self.pool.map
        self.assertEqual(pmap(sqr, range(10)), list(map(squared, range(10))))
        self.assertEqual(pmap(sqr, range(100)), list(map(squared, range(100))))

    def test_async(self):
        res = self.pool.apply_async(sqr, (7, TIMEOUT1,))
        get = TimingWrapper(res.get)
        self.assertEqual(get(), 49)
        self.assertAlmostEqual(get.elapsed, TIMEOUT1, 1)

    def test_async_callback(self):
        result = []
        res = self.pool.apply_async(sqr, (7, TIMEOUT1,), callback=lambda x: result.append(x))
        get = TimingWrapper(res.get)
        self.assertEqual(get(), 49)
        self.assertAlmostEqual(get.elapsed, TIMEOUT1, 1)
        gevent.sleep(0)  # let's the callback run
        assert result == [49], result

    def test_async_timeout(self):
        res = self.pool.apply_async(sqr, (6, TIMEOUT2 + 0.2))
        get = TimingWrapper(res.get)
        self.assertRaises(gevent.Timeout, get, timeout=TIMEOUT2)
        self.assertAlmostEqual(get.elapsed, TIMEOUT2, 1)
        self.pool.join()

    def test_imap(self):
        it = self.pool.imap(sqr, range(10))
        self.assertEqual(list(it), list(map(squared, range(10))))

        it = self.pool.imap(sqr, range(10))
        for i in range(10):
            self.assertEqual(six.advance_iterator(it), i * i)
        self.assertRaises(StopIteration, lambda: six.advance_iterator(it))

        it = self.pool.imap(sqr, range(1000))
        for i in range(1000):
            self.assertEqual(six.advance_iterator(it), i * i)
        self.assertRaises(StopIteration, lambda: six.advance_iterator(it))

    def test_imap_random(self):
        it = self.pool.imap(sqr_random_sleep, range(10))
        self.assertEqual(list(it), list(map(squared, range(10))))

    def test_imap_unordered(self):
        it = self.pool.imap_unordered(sqr, range(1000))
        self.assertEqual(sorted(it), list(map(squared, range(1000))))

        it = self.pool.imap_unordered(sqr, range(1000))
        self.assertEqual(sorted(it), list(map(squared, range(1000))))

    def test_imap_unordered_random(self):
        it = self.pool.imap_unordered(sqr_random_sleep, range(10))
        self.assertEqual(sorted(it), list(map(squared, range(10))))

    def test_empty(self):
        it = self.pool.imap_unordered(sqr, [])
        self.assertEqual(list(it), [])

        it = self.pool.imap(sqr, [])
        self.assertEqual(list(it), [])

        self.assertEqual(self.pool.map(sqr, []), [])

    def test_terminate(self):
        result = self.pool.map_async(gevent.sleep, [0.1] * ((self.size or 10) * 2))
        gevent.sleep(0.1)
        kill = TimingWrapper(self.pool.kill)
        kill()
        assert kill.elapsed < 0.5, kill.elapsed
        result.join()

    def sleep(self, x):
        gevent.sleep(float(x) / 10.)
        return str(x)

    def test_imap_unordered_sleep(self):
        # testing that imap_unordered returns items in competion order
        result = list(self.pool.imap_unordered(self.sleep, [10, 1, 2]))
        if self.pool.size == 1:
            expected = ['10', '1', '2']
        else:
            expected = ['1', '2', '10']
        self.assertEqual(result, expected)

    # https://github.com/gevent/gevent/issues/423
    def test_imap_no_stop(self):
        q = Queue()
        q.put(123)
        gevent.spawn_later(0.1, q.put, StopIteration)
        result = list(self.pool.imap(lambda _: _, q))
        self.assertEqual(result, [123])

    def test_imap_unordered_no_stop(self):
        q = Queue()
        q.put(1234)
        gevent.spawn_later(0.1, q.put, StopIteration)
        result = list(self.pool.imap_unordered(lambda _: _, q))
        self.assertEqual(result, [1234])

    # same issue, but different test: https://github.com/gevent/gevent/issues/311
    def test_imap_final_sleep(self):
        result = list(self.pool.imap(sqr, final_sleep()))
        self.assertEqual(result, [0, 1, 4])

    def test_imap_unordered_final_sleep(self):
        result = list(self.pool.imap_unordered(sqr, final_sleep()))
        self.assertEqual(result, [0, 1, 4])


class TestPool2(TestPool):
    size = 2


class TestPool3(TestPool):
    size = 3


class TestPool10(TestPool):
    size = 10


class TestPoolUnlimit(TestPool):
    size = None


class TestJoinSleep(greentest.GenericWaitTestCase):

    def wait(self, timeout):
        p = pool.Pool()
        g = p.spawn(gevent.sleep, 10)
        try:
            p.join(timeout=timeout)
        finally:
            g.kill()


class TestJoinSleep_raise_error(greentest.GenericWaitTestCase):

    def wait(self, timeout):
        p = pool.Pool()
        g = p.spawn(gevent.sleep, 10)
        try:
            p.join(timeout=timeout, raise_error=True)
        finally:
            g.kill()


class TestJoinEmpty(greentest.TestCase):
    switch_expected = False

    def test(self):
        p = pool.Pool()
        p.join()


class TestSpawn(greentest.TestCase):
    switch_expected = True

    def test(self):
        p = pool.Pool(1)
        self.assertEqual(len(p), 0)
        p.spawn(gevent.sleep, 0.1)
        self.assertEqual(len(p), 1)
        p.spawn(gevent.sleep, 0.1)  # this spawn blocks until the old one finishes
        self.assertEqual(len(p), 1)
        gevent.sleep(0.19)
        self.assertEqual(len(p), 0)


def error_iter():
    yield 1
    yield 2
    raise ExpectedException


class TestErrorInIterator(greentest.TestCase):
    error_fatal = False

    def test(self):
        p = pool.Pool(3)
        self.assertRaises(ExpectedException, p.map, lambda x: None, error_iter())
        gevent.sleep(0.001)

    def test_unordered(self):
        p = pool.Pool(3)

        def unordered():
            return list(p.imap_unordered(lambda x: None, error_iter()))

        self.assertRaises(ExpectedException, unordered)
        gevent.sleep(0.001)


def divide_by(x):
    return 1.0 / x


class TestErrorInHandler(greentest.TestCase):
    error_fatal = False

    def test_map(self):
        p = pool.Pool(3)
        self.assertRaises(ZeroDivisionError, p.map, divide_by, [1, 0, 2])

    def test_imap(self):
        p = pool.Pool(1)
        it = p.imap(divide_by, [1, 0, 2])
        self.assertEqual(it.next(), 1.0)
        self.assertRaises(ZeroDivisionError, it.next)
        self.assertEqual(it.next(), 0.5)
        self.assertRaises(StopIteration, it.next)

    def test_imap_unordered(self):
        p = pool.Pool(1)
        it = p.imap_unordered(divide_by, [1, 0, 2])
        self.assertEqual(it.next(), 1.0)
        self.assertRaises(ZeroDivisionError, it.next)
        self.assertEqual(it.next(), 0.5)
        self.assertRaises(StopIteration, it.next)


if __name__ == '__main__':
    greentest.main()
