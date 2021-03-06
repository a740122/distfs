from twisted.protocols import amp
from twisted.web.client import downloadPage
from twisted.internet.protocol import Factory
from twisted.internet import defer
import random


class ResolveError(Exception):
    """
    Chunk could not be resolved to a location.
    """


class Notify(amp.Command):
    """
    Notifcation that a chunk is available in the store.

    These are sent to the client from the agent when chunks a
    successfully downloaded and is available in the local store.
    """
    arguments = [('chunkName', amp.String())]
    

class Retrieve(amp.Command):
    """
    Tell the agent to start download the specified chunks.
    
    The agent will response as soon as it has resolved locations for
    all chunks, and the chunks has been put on the download queue.

    The agent MAY send notifcations (see L{Notify}) for chunks before
    answering the request, but then it MUST have resolved all chunks
    first.

    The C{background} flag specifies if the chunks should be retreived
    in the background.  The agent will not generate any notifications
    then.  This can be used for pre-fetching chunks.

    If a chunk could not be resolved an error is raised.
    """
    arguments = [('chunks', amp.AmpList([('chunkName', amp.String)])),
                 ('background', amp.Integer())]
    errors = {ResolveError: 'RESOLVE_ERROR'}


class Replicate(amp.Command):
    """
    Tell the agent to replicate the specified chunks.

    @param depth: replication depth, higher value means better
        availability but also increases preasure on upload.
    """
    arguments = [('chunks', amp.AmpList([('chunkName', amp.String)])),
                 ('depth', amp.Integer())]


class Shutdown(amp.Command):
    """
    Tell the agent to disconnect from the location.
    """
    

class ControlClientProtocol(amp.AMP):

    def notify(self, chunkName):
        pass
    Notify.responder(notify)


class ControlServerProtocol(amp.AMP):

    def __init__(self, resolver, directoryServer, reactor):
        self.resolver = resolver
        self.pending = list()
        self.reactor = reactor

    def connectionLost(self, reason):
        """
        Connection was lost to client for reasons specified by
        C{reason}.
        """
        # iterate all non-background downloads and cancel them
        for queueHandle in self.pending:
            queueHandle.cancel()

    def sendNotification(self, queueHandle):
        """
        Send notification to client that the chunk is now available in
        the local store.

        @param queueHandle: queue handle from the downloader
        """
        self.pending.remove(queueHandle)
        self.callRemote(Notify, queueHandle.chunkName)

    def errorNotification(self, queueHandle):
        """
        Send notification to client that a chunk could not be
        downloaded.

        @param queueHandle: queue handle from the downloader
        """
        self.pending.remove(queueHandle)
        #self.callRemote(Notify, queueHandle.chunkName)

    def cbResolve(self, results, names, background):
        """
        Callback for result from the resolver.
        """
        for ((success, locations), name) in zip(results, names):
            if not locations:
                # Send notification in the next run of the event loop.
                # This simplifies the logic a bit.
                self.reactor.callLater(0, self.sendNotification, name)
                continue

            queueHandle = self.downloader.add(chunkName, locations)
            if not background:
                doneDeferred = queueHandle.whenDone()
                doneDeferred.addCallback(self.sendNotification)
                doneDeferred.addErrback(self.errorNotification)

                # Put the handle in a list so that notications can be
                # canceled if this connection is lost.
                self.pending.append(queueHandle)
        return {}

    def retrieve(self, chunks, background):
        """
        See L{Retrieve} command. 
        """
        chunkNames = (d['chunkName'] for d in chunks)

        # XXX: iterate through and collect items that are
        # locally available.

        deferreds = list()
        for chunkName in chunkNames:
            deferreds.append(self.resolver.resolve(chunkName))

        completeDeferred = defer.DeferredList(deferreds,
                                              fireOnOneErrback=1)
        return completeDeferred.addCallback(self.cbResolve, chunkNames,
                                            background)
    Retrieve.responder(retrieve)

    def shutdown(self):
        """Shutdown service.
        """
        self.reactor.stop()
        return {}
    Shutdown.responder(shutdown)


class ControlFactory(Factory):

    def __init__(self, store, directoryService, dhtNode, resolver):
        self.store = store
        self.directoryService = directoryService
        self.dhtNode = dhtNode
        self.resolver = resolver

    def buildProtocol(self, addr):
        from twisted.internet import reactor
        return ControlServerProtocol(self.resolver,
                                     self.dhtNode,
                                     reactor)

    
