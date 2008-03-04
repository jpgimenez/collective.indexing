from zope.interface import implements
from collective.indexing.interfaces import IQueueReducer
from collective.indexing.config import INDEX, UNINDEX


class QueueReducer(object):
    """ reduce a queue of index operations """
    implements(IQueueReducer)

    def optimize(self, queue):
        """ remove redundant entries from queue;
            queue is of the form [(operator, object, attributes), ...] """
        res = {}
        for iop, obj, iattr in queue:
            op, attr = res.get(obj, (0,iattr))
            # If we are going to delete an item that was added in this transaction, ignore it
            if op == INDEX and iop == UNINDEX:
                del res[obj]
            else:
                # Operators are -1, 0 or 1 which makes it safe to add them
                op += iop
                op = min(max(op,UNINDEX), INDEX) # operator always between -1 and 1

                # Handle attributes, None means all fields, and takes presedence
                if isinstance(attr, (tuple,list)) and isinstance(iattr, (tuple,list)):
                    tmp = {}
                    tmp.update(dict.fromkeys(attr))
                    tmp.update(dict.fromkeys(iattr))
                    attr = tuple(tmp.keys())
                else:
                    attr = None

                res[obj] = (op, attr)

        return [(op, obj, attr) for obj, (op, attr) in res.items()] # almost Perl!
