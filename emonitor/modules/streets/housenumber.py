import yaml
from emonitor.extensions import db


class Housenumber(db.Model):
    """Housenumber class"""
    __tablename__ = 'housenumbers'
    __table_args__ = {'extend_existing': True}
    
    id = db.Column(db.Integer, primary_key=True)
    streetid = db.Column(db.Integer, db.ForeignKey('streets.id'))
    number = db.Column(db.String(10))
    _points = db.Column('points', db.Text)

    def __init__(self, streetid, number, points):
        self.streetid = streetid
        self.number = number
        self._points = points

    @property
    def points(self):
        """
        Get points for housenumber

        :return: yaml structure with point positions
        """
        return yaml.load(self._points)

    @staticmethod
    def getHousenumbers(id=0):
        """
        Get list of all housenumbers, filtered by paramters

        :param optional id: id of housenumber, *0* for all
        :return: list of :py:class:`emonitor.modules.streets.housenumber.Housenumber`
        """
        if id == 0:
            return db.session.query(Housenumber).all()
        else:
            return db.session.query(Housenumber).filter_by(id=int(id)).first()
