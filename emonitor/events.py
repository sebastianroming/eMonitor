import threading
import traceback
import logging
from .extensions import classes

logger = logging.getLogger(__name__)


class Event:
    """ event class for global events """
    events = []
    globalhandlers = {}
    app = None

    def __init__(self, name, handlers=list(), parameters=list()):
        self.name = name
        self.handlers = handlers
        self.parameters = parameters

    def __repr__(self):
        return '<Event %s>' % self.name

    def handle(self, args):  # handle defined handlers (db) for current event
        if 'mode' not in args.keys():
            args['mode'] = 'active'

        for db_handler in self.getHandlers():
            for handler in [hdl for hdl in self.getHandlerList() if hdl[0] == db_handler.handler or hdl[0] == '*']:
                if handler[1](self.name, args):
                    pass

    def addHandler(self, classname):
        self.handlers.append(classname)

    def getHandlerList(self):  # returns all possible handlers for event (global + special)
        return Event.globalhandlers.values() + self.handlers

    def getHandlers(self, handlerid=0):  # returns defined handlers (db) for event
        if handlerid != 0:
            if classes.get('eventhandler'):
                return classes.get('eventhandler').getEventhandlers(id=handlerid)
        else:
            if classes.get('eventhandler'):
                return classes.get('eventhandler').getEventhandlers(event=self.name)
        return []

    @staticmethod
    def addEvent(name, handlers=list(), parameters=list()):
        Event.events.append(Event(name, handlers, parameters))

    @staticmethod
    def getEvents(name=""):
        if name == "":
            return Event.events
        else:
            return [e for e in Event.events if e.name == name][0]

    @staticmethod
    def addHandlerClass(name, classid, func, params):
        if name == "*" and classid not in Event.globalhandlers.keys():  # add global * handlers
            Event.globalhandlers[classid] = [classid, func, params]

        for ev in [e for e in Event.events if e.name == name]:
            ev.handlers.append([classid, func, params])
        return

    @staticmethod
    def raiseEvent(name, *kwargs):
        logger.info('raiseEvent %s' % name)
        logger.debug('  > arguments %s' % kwargs[0])
        action = RunEvent([e for e in Event.events if e.name == name], kwargs[0], Event.app.logger)
        action.start()

    @staticmethod
    def init_app(app):
        Event.app = app
        

class RunEvent(threading.Thread):
    """ event handler in threads """
    def __init__(self, eventhandler, kwargs, logger):
        threading.Thread.__init__(self)
        self.eventhandler = eventhandler
        self.kwargs = kwargs
        self.logger = logger

    def run(self):
        # execute handlers
        handler = None
        for handler in self.eventhandler:
            try:
                logger.info('try handleEvent %s' % handler.name)
                handler.handle(self.kwargs)
            except:
                logger.error('handleEvent %s: %s' % (handler.name, traceback.format_exc()))
                raise
        if handler:
            logger.info('eventDone %s' % handler.name)
