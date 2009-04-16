from logging import getLogger
from threading import local
from zope.interface import implements
from zope.component import queryUtility, getUtilitiesFor
from Acquisition import aq_base, aq_inner

from collective.indexing.interfaces import IIndexQueue
from collective.indexing.interfaces import IIndexQueueProcessor
from collective.indexing.interfaces import IQueueReducer
from collective.indexing.config import INDEX, REINDEX, UNINDEX
from collective.indexing.transactions import QueueTM

debug = getLogger('collective.indexing.queue').debug


localQueue = None
processing = set()


def getQueue():
    """ return a (thread-local) queue object, create one if necessary """
    global localQueue
    if localQueue is None:
        localQueue = IndexQueue()
    return localQueue


def processQueue():
    """ process the queue (for this thread) immediately """
    queue = getQueue()
    processed = 0
    if queue.length() and not queue in processing:
        debug('auto-flushing %d items: %r', queue.length(), queue.getState())
        processing.add(queue)
        processed = queue.process()
        processing.remove(queue)
    return processed


def wrap(obj):
    """ the indexing key, i.e. the path to the object in the case of the
        portal catalog, might have changed while the unindex operation was
        delayed, for example due to renaming the object;  it was probably not
        such a good idea to use a key that can change in the first place, but
        to work around this a proxy object is used, which can provide the
        original path;  of course, access to other attributes must still be
        possible, since alternate indexers (i.e. solr etc) might use another
        unique key, usually the object's uid;  also the inheritence tree
        must match """
    if getattr(aq_base(obj), 'getPhysicalPath', None) is None:
        return obj

    class PathWrapper(obj.__class__):

        def __init__(self):
            self.__dict__.update(dict(
                context = obj,
                path = obj.getPhysicalPath(),
                REQUEST = getattr(obj, 'REQUEST', None)))

        def __getattr__(self, name):
            return getattr(aq_inner(self.context), name)

        def __hash__(self):
            return hash(self.context)   # make the wrapper transparent...

        def getPhysicalPath(self):
            return self.path

    return PathWrapper()


class IndexQueue(local):
    """ an indexing queue """
    implements(IIndexQueue)

    def __init__(self):
        self.queue = []
        self.tmhook = None

    def hook(self):
        """ register a hook into the transaction machinery if that hasn't
            already been done;  this is to make sure the queue's processing
            method gets called back just before the transaction is about to
            be committed """
        if self.tmhook is None:
            self.tmhook = QueueTM(self).register
        self.tmhook()

    def index(self, obj, attributes=None):
        assert obj is not None, 'invalid object'
        debug('adding index operation for %r', obj)
        self.queue.append((INDEX, obj, attributes))
        self.hook()

    def reindex(self, obj, attributes=None):
        assert obj is not None, 'invalid object'
        debug('adding reindex operation for %r', obj)
        self.queue.append((REINDEX, obj, attributes))
        self.hook()

    def unindex(self, obj):
        assert obj is not None, 'invalid object'
        debug('adding unindex operation for %r', obj)
        self.queue.append((UNINDEX, wrap(obj), None))
        self.hook()

    def setHook(self, hook):
        assert callable(hook), 'hook must be callable'
        debug('setting hook to %r', hook)
        self.tmhook = hook

    def getState(self):
        return list(self.queue)     # better return a copy... :)

    def setState(self, state):
        assert isinstance(state, list), 'state must be a list'
        debug('setting queue state to %r', state)
        self.queue = state

    def length(self):
        """ return number of currently queued items;  please note that
            we cannot use `__len__` here as this will cause test failures
            due to the way objects are compared """
        return len(self.queue)

    def optimize(self):
        reducer = queryUtility(IQueueReducer)
        if reducer is not None:
            self.setState(reducer.optimize(self.getState()))

    def process(self):
        utilities = list(getUtilitiesFor(IIndexQueueProcessor))
        debug('processing queue using %r', utilities)
        processed = 0
        for name, util in utilities:
            util.begin()
        # TODO: must the queue be handled independently for each processor?
        self.optimize()
        for op, obj, attributes in self.queue:
            for name, util in utilities:
                if op == INDEX:
                    util.index(obj, attributes)
                elif op == REINDEX:
                    util.reindex(obj, attributes)
                elif op == UNINDEX:
                    util.unindex(obj)
                else:
                    raise 'InvalidQueueOperation', op
            processed += 1
        debug('finished processing %d items...', processed)
        self.clear()
        return processed

    def commit(self):
        for name, util in getUtilitiesFor(IIndexQueueProcessor):
            debug('committing changes queue using %r', util)
            util.commit()

    def abort(self):
        for name, util in getUtilitiesFor(IIndexQueueProcessor):
            debug('aborting changes queue using %r', util)
            util.abort()
        self.clear()

    def clear(self):
        debug('clearing %d queue item(s)', len(self.queue))
        del self.queue[:]
        self.tmhook = None      # release transaction manager...