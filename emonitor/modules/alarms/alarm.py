import os
import time
import datetime
import shutil
import requests
import re
import yaml
from collections import OrderedDict
import logging

from flask import current_app, flash, render_template, abort
from emonitor.extensions import babel, db, classes, events, monitorserver, scheduler, signal
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.sql.functions import count
import emonitor.modules.alarms.alarmutils as alarmutils
from emonitor.modules.alarms.alarmhistory import AlarmHistory
from emonitor.modules.alarms.alarmattribute import AlarmAttribute

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

USE_NOMINATIM = 0
LASTALARM = 0.0  # timestamp ini millies


class Alarm(db.Model):
    """Alarm class"""
    __tablename__ = 'alarms'
    __table_args__ = {'extend_existing': True}

    ALARMSTATES = {'0': 'created', '1': 'active', '2': 'done'}
    """
    - 0: alarm *created*
    - 1: alarm *active*
    - 2: alarm *done*
    - 3: alarm *archived* (not in list, only for admin area)
    """
    ALARMTYPES = {'1': 'fax', '2': 'manual'}
    """
    - 1: alarm by *fax* created
    - 2: alarm *manual* created
    """
    ROUTEURL = "http://www.yournavigation.org/api/1.0/gosmore.php"
    """
    URL for routing webservice
    """

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DATETIME)
    _key = db.Column('key', db.Text)
    type = db.Column(db.Integer, default=0)
    state = db.Column(db.Integer, default=0)
    attributes = db.relationship("AlarmAttribute", collection_class=attribute_mapped_collection('name'), cascade="all, delete-orphan")
    history = db.relationship(AlarmHistory.__name__, backref="alarms", lazy='joined', cascade="all, delete-orphan")

    # additional properties defined in alarmutils
    endtimestamp = property(alarmutils.get_endtimestamp)
    cars1 = property(alarmutils.get_cars1)
    cars2 = property(alarmutils.get_cars2)
    material = property(alarmutils.get_material, alarmutils.set_material)
    city = property(alarmutils.get_city, alarmutils.set_city)
    key = property(alarmutils.get_key)
    street = property(alarmutils.get_street, alarmutils.set_street)
    street2 = property(alarmutils.get_street2)
    streetno = property(alarmutils.get_streetno)

    housenumber = property(alarmutils.get_housenumber)

    object = property(alarmutils.get_object, alarmutils.set_object)
    person = property(alarmutils.get_person)
    priority = property(alarmutils.get_priority)
    remark = property(alarmutils.get_remark)
    lat = property(alarmutils.get_lat)
    lng = property(alarmutils.get_lng)
    zoom = property(alarmutils.get_zoom)
    position = property(alarmutils.get_position, alarmutils.set_position)
    marker = property(alarmutils.get_marker)

    def __init__(self, timestamp, key, type, state):
        self.timestamp = timestamp
        self._key = key
        self.type = type  # 1:automatic/fax, 2: manual
        self.state = state  # 1: active, 0:created, 2:done, 3:archived

    def get(self, attrname, default=''):
        """
        Getter for attribute names

        :param attrname: name of attribute
        :param optional default: deliver default value if not stored in database
        :return: value of attribute
        """
        if attrname in self.attributes:
            return self.attributes[attrname].value
        return default

    def set(self, attrname, value):
        """
        Setter for attributes

        :param attrname: attribute name
        :param value: value
        """
        if attrname in self.attributes:
            self.attributes[attrname].value = value
        else:
            self.attributes[attrname] = AlarmAttribute(attrname, value)

    def addHistory(self, name, value, dtime=None):
        """
        Add history entry for alarm to store actions of alarm using
        :py:class:`emonitor.modules.alarms.alarmhistory.AlarmHistory`

        :param name: name of event
        :param value: value of history entry
        :param optional dtime: timestamp of history entry (now)
        """
        if not dtime:
            dtime = datetime.datetime.now()
        self.history.append(AlarmHistory(name, value, dtime))

    def getAdditionalLayers(self):
        """
        Get additional layers of default map definition of current alarm

        :return: list of :py:class:`emonitor.modules.mapitems.mapitem.MapItem` objects
        """
        cat = self.key.category
        items = []
        for itemtype in self.getMap().getMapItemDefinitions():
            for r in itemtype['key']:
                regex = re.compile(r)
                if regex.search(cat):
                    items.append(itemtype)
        return items

    @staticmethod
    def getMap():
        """
        Returns default map defined in eMonitor

        :return: :py:class:`emonitor.modules.maps.map.Map`
        """
        return classes.get('map').getDefaultMap()

    @staticmethod
    def getAlarms(id=0, days=0, state=-1):
        """
        Get list of alarm objects filtered by parameters

        :param optional id: id of alarm or 0 for all alarms
        :param optional days: number of days since alarmdate
        :param optional state: -1 for alarms of all states, see :py:attr:`emonitor.modules.alarms.alarm.Alarm.ALARMSTATES` for value definition
        :return: list or single object :py:class:`emonitor.modules.alarms.alarm.Alarm`
        """
        if id != 0:
            return db.session.query(Alarm).filter_by(id=id).first()
        elif days != 0:  # filter last days, 0 = all days
            if int(state) == -1:
                return db.session.query(Alarm).filter(Alarm.timestamp > (datetime.datetime.now() - datetime.timedelta(days=days))).order_by('alarms.timestamp desc').all()
            else:
                return db.session.query(Alarm).filter(Alarm.timestamp > (datetime.datetime.now() - datetime.timedelta(days=days)), Alarm.state == state).order_by('alarms.timestamp desc').all()
        else:
            if int(state) == -1:  # all states
                return db.session.query(Alarm).order_by('alarms.timestamp desc').all()
            else:
                return db.session.query(Alarm).filter(Alarm.state == state).order_by('alarms.timestamp desc').all()

    @staticmethod
    def getAlarmCount(days=0):
        """
        Get number of alarms, grouped by state

        :param optional days: 0 for all alarms, since days else
        :return: list grouped by state
        """
        if days != 0:
            return db.session.query(Alarm.state, count(Alarm.id)).filter(Alarm.timestamp > (datetime.datetime.now() - datetime.timedelta(days=days))).order_by('alarms.timestamp desc').group_by(Alarm.state).all()
        else:
            return db.session.query(Alarm.state, count(Alarm.id)).group_by(Alarm.state).all()

    @staticmethod
    def getActiveAlarms():
        """
        Get list of all active alarms

        :return: list or :py:class:`emonitor.modules.alarms.alarm.Alarm`
        """
        return db.session.query(Alarm).filter_by(state=1).order_by('alarms.timestamp desc').all()

    @staticmethod
    def changeStates(state):
        """
        Set states of ALL alarms to given state

        :param state: state as :py:attr:`emonitor.modules.alarms.alarm.Alarm.ALARMSTATES`
        """
        for alarm in classes.get('alarm').getAlarms(0):
            Alarm.changeState(alarm.id, state)

    def getRouting(self):
        if self.get('routing', '') == "":  # load from webservice if not stored
            routingdata = alarmutils.getAlarmRoute(self)
            if len(routingdata['coordinates']) > 0:
                self.set('routing', yaml.safe_dump(routingdata, encoding="UTF-8"))
                db.session.commit()
        data = yaml.load(self.get('routing'))
        if 'error' in data:
            data['errormessage'] = babel.gettext(u'alarms.routingerror')
        return data

    @staticmethod
    def changeState(id, state):
        """
        Change state of alarm with given id. Adds entry in alarmhistory and sends signal

        :param id: id of alarm
        :param state: new state as :py:attr:`emonitor.modules.alarms.alarm.Alarm.ALARMSTATES`
        """
        global LASTALARM
        alarm = classes.get('alarm').getAlarms(id)
        if not alarm:
            return []
        alarm.state = state
        try:
            alarm.addHistory('autochangeState', Alarm.ALARMSTATES[str(state)])
        except KeyError:
            alarm.addHistory('autochangeState', 'archived')
        db.session.commit()

        if state == 1:  # activate alarm
            c = []
            for a in Alarm.getActiveAlarms():  # check cars
                if a.id == id:
                    continue
                c.extend(set(a.cars1).intersection(set(alarm.cars1)))
                c.extend(set(a.cars2).intersection(set(alarm.cars2)))
                c.extend(set(a.material).intersection(set(alarm.material)))

            if time.time() - LASTALARM < 60.0:
                try:
                    ids = [a.id for a in Alarm.getActiveAlarms()]
                    for j in [job for job in scheduler.get_jobs() if job.name == 'changeLayout']:
                        for i in ids:
                            if "'alarmid', %s" % i in str(j.args):  # layout changes for given alarm
                                scheduler.unschedule_job(j)
                except:
                    logger.error('%s' % [a.id for a in Alarm.getActiveAlarms()])
            LASTALARM = time.time() + 2.0

            if alarm.get('alarmtype', '') != '':
                scheduler.add_job(events.raiseEvent, next_run_time=datetime.datetime.fromtimestamp(LASTALARM), args=['alarm_added.%s' % alarm.get('alarmtype'), {'alarmid': id}])
            else:
                scheduler.add_job(events.raiseEvent, next_run_time=datetime.datetime.fromtimestamp(LASTALARM), args=['alarm_added', {'alarmid': id}])

            # close alarm after alarms.autoclose, default 30 minutes
            if alarm.state == 1:  # autoclose only automatic alarms
                scheduler.add_job(Alarm.changeState, next_run_time=datetime.datetime.fromtimestamp(
                    LASTALARM + float(classes.get('settings').get('alarms.autoclose', 1800))), args=[id, 2])
            try:
                flash(babel.gettext(u'alarms.statechangeactivated'), 'alarms.activate')
            except:
                pass
            finally:
                monitorserver.sendMessage('0', 'reset')  # refresh monitor layout
                signal.send('alarm', 'changestate', newstate=1)
                return list(set(c))

        elif state == 2:  # close alarm
            LASTALARM = 0.0

            for j in scheduler.get_jobs():
                if j.name == 'changeLayout' and "'alarmid', %s" % id in str(j.args):  # layout changes for given alarm
                    scheduler.remove_job(j.id)
                elif j.name == 'changeState' and j.args[0] == id:  # state changes for given alarm
                    scheduler.remove_job(j.id)

            monitorserver.sendMessage('0', 'reset')  # refresh monitor layout
            signal.send('alarm', 'changestate', newstate=2)
            return []

        elif state == 3:  # archive alarm
            signal.send('alarm', 'changestate', newstate=3)
            return []

        signal.send('alarm', 'changestate', newstate=state)

    @staticmethod
    def getExportData(exportformat, **params):
        """
        Export alarm to given format

        :param exportformat: *.html*, *.png*
        :param params:
          - style: exportstyle: *alarmmap*, *routemap*

          - filename: name of exportfile

        :return: alarm in correct format
        """
        if params['id'] and params:
            alarm = Alarm.getAlarms(params['id'])
            if not alarm:  # create dummy alarm
                alarm = Alarm(datetime.datetime.now(), '', 2, 0)
                alarm.position = dict(lat=classes.get('settings').get('defaultLat'), lng=classes.get('settings').get('defaultLng'))
                alarm.set('id.key', '1')
                alarm.set('id.address', '1')
                alarm.set('id.city', '1')
                alarm.set('remark', 'TEST TEST TEST')
            if alarm:
                if exportformat == '.html' and 'style' in params:  # build html-template
                    params.update({'alarm': alarm})
                    return render_template('print.%s.html' % params['style'], **params)

                elif exportformat == '.png':  # send image file

                    if params['style'].startswith('alarmmap'):  # deliver map for alarmid
                        from emonitor.modules.maps.map_utils import getAlarmMap
                        args = dict()
                        if params['style'] != 'alarmmap':
                            args = dict(style=params['style'].split('_')[-1])
                        return getAlarmMap(alarm, current_app.config.get('PATH_TILES'), **args)

                    elif params['style'] == 'routemap':  # deliver map with route
                        from emonitor.modules.maps.map_utils import getAlarmRoute
                        return getAlarmRoute(alarm, current_app.config.get('PATH_TILES'))

                    if 'filename' in params and os.path.exists("%s/inc/%s.png" % (os.path.abspath(os.path.dirname(__file__)), params['filename'])):
                        with open("%s/inc/%s.png" % (os.path.abspath(os.path.dirname(__file__)), params['filename']), 'rb') as f:
                            return f.read()
        abort(404)

    @staticmethod
    def handleEvent(eventname, *kwargs):
        """
        Eventhandler for alarm class

        :param eventname: name of event
        :param kwargs: parameter list: error, fields, filename, id, incomepath, mode, time
        :return: all kwargs
        """
        import emonitor.webapp as wa
        global LASTALARM

        alarm_fields = dict()
        stime = time.time()
        alarmtype = None
        stime = time.time()
        for t in classes.get('alarmtype').getAlarmTypes():
            if re.search(t.keywords.replace('\r\n', '|'), unicode(kwargs[0]['text'], errors='ignore')):
                alarm_fields = t.interpreterclass().buildAlarmFromText(t, kwargs[0]['text'])
                if u'error' in alarm_fields.keys():
                    kwargs[0]['error'] = alarm_fields['error']
                    del alarm_fields['error']
                alarmtype = t
                break

        # copy file -> original name
        shutil.copy2(kwargs[0]['incomepath'] + kwargs[0]['filename'],
                     '%s%s' % (wa.config.get('PATH_DONE'), kwargs[0]['filename']))
        try:  # remove file
            os.remove(kwargs[0]['incomepath'] + kwargs[0]['filename'])
        except:
            pass
        logger.debug('alarm_fields: %s' % alarm_fields)

        if len(alarm_fields) == 0:  # no alarmfields found
            kwargs[0]['id'] = 0
            logger.error('no alarm fields found.')
            return kwargs

        kwargs[0]['fields'] = ''
        for k in alarm_fields:
            kwargs[0]['fields'] += '\n-%s:\n  %s' % (k, alarm_fields[k])

        if not alarmtype:  # alarmtype not found
            kwargs[0]['id'] = 0
            kwargs[0]['error'] = 'alarmtype not found'
            logger.error('alarmtype not found.')
            return kwargs

        # position
        _position = dict(lat=u'0.0', lng=u'0.0')
        if USE_NOMINATIM == 1:
            try:
                url = 'http://nominatim.openstreetmap.org/search'
                params = 'format=json&city=%s&street=%s' % (alarm_fields['city'][0], alarm_fields['address'][0])
                if 'streetno' in alarm_fields:
                    params += ' %s' % alarm_fields['streetno'][0].split()[0]  # only first value
                r = requests.get('%s?%s' % (url, params))
                _position = dict(lat=r.json()[0]['lat'], lng=r.json()[0]['lon'])
            except:
                pass

        # create alarm object
        if 'key' not in alarm_fields.keys():
            if alarmtype.translation(u'_bma_main_') in alarm_fields['remark'][0] or alarmtype.translation(u'_bma_main_') in alarm_fields['person'][0]:
                alarmkey = classes.get('alarmkey').getAlarmkeysByName(alarmtype.translation(u'_bma_'))
                if len(alarmkey) > 0:
                    alarm_fields['key'] = ('%s: %s' % (alarmkey[0].category, alarmkey[0].key), str(alarmkey[0].id))
                else:
                    alarm_fields['key'] = (alarmtype.translation(u'_bma_key_'), u'0')

        if 'time' in alarm_fields and alarm_fields['time'][1] == 1:  # found correct time
            t = datetime.datetime.strptime(alarm_fields['time'][0], '%d.%m.%Y - %H:%M:%S')
        else:
            t = datetime.datetime.now()

        alarm = Alarm(t, alarm_fields['key'][0], 1, 0)
        db.session.add(alarm)
        alarm.set('id.key', alarm_fields['key'][1])
        alarm.material = dict(cars1='', cars2='', material='')  # set required attributes
        alarm.set('marker', '0')
        alarm.set('filename', kwargs[0]['filename'])
        alarm.set('priority', '1')  # set normal priority
        alarm.set('alarmtype', alarmtype.name)  # set checker name
        alarm.state = 1

        # city
        if 'city' in alarm_fields and alarm_fields['city'][1] != 0:
            alarm.city = classes.get('city').getCities(alarm_fields['city'][1])
            if alarm_fields['address'][1] != 0:
                alarm.street = classes.get('street').getStreet(alarm_fields['address'][1])
        else:  # city not found -> build from fax
            url = 'http://nominatim.openstreetmap.org/search'
            params = 'format=json&city=%s&street=%s' % (alarm_fields['city'][0].split()[0], alarm_fields['address'][0])
            if 'streetno' in alarm_fields:
                params += ' %s' % alarm_fields['streetno'][0].split()[0]  # only first value
                alarm.set('streetno', alarm_fields['streetno'][0])

            r = requests.get('%s?%s' % (url, params))
            logger.debug('load address data from nomination with parameters: city=%s street=%s' % (alarm_fields['city'][0].split()[0], alarm_fields['address'][0]))
            try:
                _position = dict(lat=r.json()[0]['lat'], lng=r.json()[0]['lon'])
                alarm.position = _position
            except:
                pass

            alarm.set('city', alarm_fields['city'][0].split()[0])
            alarm.set('id.city', alarm_fields['city'][1])
            alarm.set('address', alarm_fields['address'][0])
            if alarm_fields['address'][1] != 0:
                alarm.street = classes.get('street').getStreet(id=alarm_fields['address'][1])

            if 'cars' in alarm_fields:  # add cars found in material
                for _c in alarm_fields['cars'][1].split(';'):
                    alarm.set('k.cars1', alarm.get('k.cars1') + ';' + _c)

        # street / street2
        if 'address' in alarm_fields and alarm_fields['address'][0] != '':
            # check correct city -> change if street has different city
            if len(str(alarm_fields['address'][1]).split(';')) > 0 and alarm_fields['address'][1] != 0:
                _c = []

                for s in str(alarm_fields['address'][1]).split(';'):
                    _s = classes.get('street').getStreet(id=s)
                    if _s.cityid and _s.cityid not in _c and _s.cityid == alarm_fields['city'][1]:
                        _c.append(_s.cityid)
                        alarm.street = _s
                        if 'object' in alarm_fields and str(alarm_fields['object'][1]) == '0':
                            if 'lat' not in alarm_fields and 'lng' not in alarm_fields:
                                alarm.position = dict(lat=_s.lat, lng=_s.lng, zoom=_s.zoom)
                                if _position['lat'] != u'0.0' and _position['lng'] != u'0.0':  # set marker if nominatim delivers result
                                    alarm.position = _position
                                    alarm.set('marker', '1')
            else:  # add unknown street
                alarm.set('id.address', 0)
                alarm.set('address', alarm_fields['address'][0])
        # houseno
        if 'streetno' in alarm_fields.keys():
            alarm.set('streetno', alarm_fields['streetno'][0])
            if 'id.streetno' in alarm_fields and 'lat' in alarm_fields and 'lng' in alarm_fields:
                alarm.position = dict(lat=alarm_fields['lat'][0], lng=alarm_fields['lng'][0])

            if 'zoom' in alarm_fields.keys():
                alarm.set('zoom', alarm_fields['zoom'][0])

        # crossing
        if 'crossing' in alarm_fields and alarm_fields['crossing'][0] != '':
            if 'crossing' in alarm_fields and alarm_fields['address'][1] != alarm_fields['crossing'][1]:
                alarm.set('id.address2', alarm_fields['crossing'][1])
                alarm.set('address2', alarm_fields['crossing'][0])
            else:
                alarm.set('id.address2', '0')
                alarm.set('address2', alarm_fields['crossing'][0])

        # addresspart
        if 'addresspart' in alarm_fields and alarm_fields['addresspart'][0] != '' and alarm_fields['addresspart'][0] != alarm_fields['address'][0]:
            if alarm_fields['addresspart'][1] > 0:
                if len(str(alarm_fields['addresspart'][1]).split(';')) > 0:
                    _c = []

                    for s in str(alarm_fields['addresspart'][1]).split(';'):
                        try:
                            _s = classes.get('street').getStreets(id=s)
                            if _s.cityid not in _c and _s.cityid == alarm_fields['city'][1]:
                                _c.append(_s.cityid)
                                alarm.set('id.address2', _s.id)
                        except:
                            pass
                else:
                    alarm.set('id.address2', alarm_fields['addresspart'][1])
            else:
                alarm.set('id.address2', '0')
            alarm.set('address2', alarm_fields['addresspart'][0])

        # person
        if 'person' in alarm_fields and alarm_fields['person'][0] != '':
            alarm.set('person', alarm_fields['person'][0])
        # alarmplan
        if 'alarmplan' in alarm_fields and alarm_fields['alarmplan'][0] != '':
            alarm.set('alarmplan', alarm_fields['alarmplan'][0])

        # alarmobject
        _ao = None
        if 'object' in alarm_fields and alarm_fields['object'][0] != '':
            alarm.set('object', alarm_fields['object'][0])
            alarm.set('id.object', alarm_fields['object'][1])
            # alarmplan from object
            if alarm_fields['object'][1] != 0:
                _ao = classes.get('alarmobject').getAlarmObjects(id=alarm_fields['object'][1])

            try:
                if _ao.alarmplan != 0:
                    alarm.set('alarmplan', _ao.alarmplan)
                if _ao.streetid != alarm_fields['address'][1]:  # street config from alarmobject
                    alarm.street = classes.get('street').getStreets(id=_ao.streetid)
                    if _ao.streetno == "":
                        alarm.set('streetno', alarm_fields['streetno'][0])
                    else:
                        alarm.set('streetno', _ao.streetno)
                alarm.position = dict(lat=_ao.lat, lng=_ao.lng, zoom=_ao.zoom)
            except:
                pass

        # remark
        if 'remark' in alarm_fields and alarm_fields['remark'][0] != '':
            alarm.set('remark', alarm_fields['remark'][0])
            if alarmtype.translation(u'_bma_main_') in alarm_fields['remark'][0] or alarmtype.translation(u'_bma_main_') in alarm_fields['person'][0]:
                alarmkey = classes.get('alarmkey').getAlarmkeysByName(alarmtype.translation(u'_bma_'))
                if len(alarmkey) > 0:
                    alarm.set('id.key', alarmkey[0].id)
                    alarm._key = u'%s: %s' % (alarmkey[0].category, alarmkey[0].key)
                else:
                    alarm.set('id.key', '0')
                    alarm._key = alarmtype.translation(u'_bma_key_')
        # additional remarks
        if 'remark2' in alarm_fields and alarm_fields['remark2'][0] != '':
            alarm.set('remark', u'%s\n%s' % (alarm.get('remark'), alarm_fields['remark2'][0]))

        # material
        if alarm.get('id.key') != 0 and 'city' in alarm_fields:  # found key with aao

            if alarm_fields['city'][1] != 0:  # default city
                if classes.get('department').getDepartments(alarm.city.dept).defaultcity == alarm_fields['city'][1]:  # default city for dep
                    if 'material' in alarm_fields:
                        if str(alarm_fields['material'][1])[0] == '0':  # default cars for aao
                            try:
                                alarm.material = dict(cars1=u','.join([str(c.id) for c in alarm.key.getCars1(alarm.street.city.dept)]), cars2=u','.join([str(c.id) for c in alarm.key.getCars2(alarm.street.city.dept)]), material=u','.join([str(c.id) for c in alarm.key.getMaterial(alarm.street.city.dept)]))
                            except AttributeError:
                                alarm.material = dict(cars1=u','.join([str(c.id) for c in alarm.key.getCars1(alarm.city.dept)]), cars2=u','.join([str(c.id) for c in alarm.key.getCars2(alarm.city.dept)]), material=u','.join([str(c.id) for c in alarm.key.getMaterial(alarm.city.dept)]))

                        for _c in alarm_fields['material'][1].split(','):  # add additional cars
                            if _c != '0' and _c not in alarm.get('k.cars1').split(','):
                                alarm.set('k.cars1', u'{},{}'.format(alarm.get('k.cars1'), _c))

                else:  # only alarmed material
                    alarm.material = dict(cars1=alarm_fields['material'][1])

            else:  # else city
                if alarm_fields['material'][1] == u'0':  # default cars for aao
                    alarm.material = dict(cars1=u','.join([str(c.id) for c in alarm.key.getCars1(alarm.city.dept)]), cars2=u','.join([str(c.id) for c in alarm.key.getCars2(alarm.city.dept)]), material=u','.join([str(c.id) for c in alarm.key.getMaterial(alarm.city.dept)]))
                else:
                    alarm.material = dict(cars1=u','.join(list(OrderedDict.fromkeys(filter(lambda x: x != '0', str(alarm_fields['material'][1]).split(','))))))

        else:  # default aao of current department (without aao)
            if alarm_fields['city'][1] != 0:  # found city -> use default aao
                c = classes.get('city').get_byid(alarm_fields['city'][1]).dept
                akc = classes.get('alarmkey').getDefault(c)
                alarm.material = dict(cars1=u",".join([str(c.id) for c in akc.cars1]), cars2=u",".join([str(c.id) for c in akc.cars2]), material=u",".join([str(c.id) for c in akc.materials]))

            l = (u'%s,%s,%s' % (alarm.get('k.cars1'), alarm.get('k.cars2'), alarm.get('k.material'))).split(',')
            if len(set(str(alarm_fields['material'][1]).split(',')).intersection(set(l))) == 0:
                _dep = classes.get('department').getDefaultDepartment()
                for c in alarm_fields['material'][1].split(','):
                    if c == u'0':  # default of home department needed
                        alarm.material = dict(cars1=u",".join([str(c.id) for c in alarm.key.getCars1(_dep.id)]), cars2=u",".join([str(c.id) for c in alarm.key.getCars2(_dep.id)]), material=u",".join([str(c.id) for c in alarm.key.getMaterial(_dep.id)]))
                        break
                if u'0' not in alarm_fields['material'][1]:  # only single car needed
                    alarm.set('k.cars1', u'%s,%s' % (alarm_fields['material'][1], alarm.get('k.cars1')))

        if _ao and _ao.hasOwnAAO():  # use aao of current object
            alarm.material = dict(cars1=u",".join([str(c.id) for c in _ao.getCars1()]), cars2=u",".join([str(c.id) for c in _ao.getCars2()]), material=u",".join([str(c.id) for c in _ao.getMaterial()]))

        if 'time' not in kwargs[0]:
            kwargs[0]['time'] = []
        etime = time.time()
        kwargs[0]['time'].append('alarm creation done in %s sec.' % (etime - stime))

        if kwargs[0]['mode'] != 'test':
            db.session.commit()
            signal.send('alarm', 'added', alarmid=alarm.id)
            Alarm.changeState(alarm.id, 1)  # activate alarm
            logger.info('alarm created with id %s (%s)' % (alarm.id, (etime - stime)))
        else:
            kwargs[0]['fields'] += '\n\n--------------------------\nALARM-Object\n'
            _cdict = classes.get('car').getCarsDict()
            for a in alarm.attributes:
                try:
                    if a in ['k.cars1', 'k.cars2', 'k.material']:
                        kwargs[0]['fields'] += '\n-%s:\n  %s -> %s' % (a, alarm.get(a), ", ".join([_cdict[int(_c)].name for _c in alarm.get(a).split(',') if _c != ""]))
                    elif a in 'id.key':
                        _k = classes.get('alarmkey').getAlarmkeys(alarm.get(a))
                        kwargs[0]['fields'] += '\n-%s:\n  %s -> %s: %s' % (a, alarm.get(a), _k.category, _k.key)
                    elif a == 'id.address':
                        kwargs[0]['fields'] += '\n-%s:\n  %s -> %s' % (a, alarm.get(a), classes.get('street').getStreet(id=alarm.get(a)).name)
                    else:
                        kwargs[0]['fields'] += '\n-%s:\n  %s' % (a, alarm.get(a))
                except (AttributeError, KeyError):
                    kwargs[0]['fields'] += '\n-%s:\n  %s (error)' % (a, alarm.get(a))
            kwargs[0]['id'] = '-0'  # add dummy id
            db.session.rollback()
            logger.info('alarm created in TESTMODE (%s)' % (etime - stime))
            return kwargs

        if alarm:
            kwargs[0]['id'] = alarm.id
        else:
            kwargs[0]['id'] = 0
        return kwargs
