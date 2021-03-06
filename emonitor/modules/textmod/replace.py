import time
import re
from emonitor.extensions import db, classes


class Replace(db.Model):
    """Replace class"""
    __tablename__ = 'replace'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text)
    replace = db.Column(db.Text)
    
    def __init__(self, text, replace):
        self.text = text
        self.replace = replace

    @staticmethod
    def getReplacements(id=0):
        """
        Get list of replacements or filtered by id

        :param optional id: id of replacement or *0* for all
        :return: list of :py:class:`emonitor.modules.textmod.replace.Replace`
        """
        if id == 0:
            return db.session.query(Replace).order_by('id')
        else:
            return db.session.query(Replace).filter_by(id=id)[0]
            
    @staticmethod
    def handleEvent(eventname, *kwargs):
        """
        Event handler for replacements

        :param eventname: *file_added*
        :param kwargs: *time*, *text*
        :return: kwargs
        """
        hdl = [hdl for hdl in classes.get('eventhandler').getEventhandlers(event=eventname) if hdl.handler == 'emonitor.modules.textmod.ocr.Ocr'][0]
        in_params = [v[1] for v in hdl.getParameterValues('in')]  # required parameters for method

        if sorted(in_params) != sorted(list(set(in_params) & set(kwargs[0].keys()))):
            if 'time' not in kwargs[0]:
                kwargs[0]['time'] = []
            kwargs[0]['time'].append('replace: missing parameters for replace, nothing done.')
            return kwargs
        else:
            stime = time.time()
            text = ''
            for l in kwargs[0]['text'].split("\n"):
                if "__" in l or l.strip() == "" or "===" in l or l.strip() == "":  # leave empty and lines
                    continue
                text += l + " \n"

            for r in Replace.getReplacements():
                text = re.sub(r.text.encode('utf-8'), r.replace.encode('utf-8'), text)

            kwargs[0]['text'] = text
            t = time.time() - stime
            
        if 'time' not in kwargs[0]:
            kwargs[0]['time'] = []
        kwargs[0]['time'].append('replace: replace done in %s sec.' % t)
        
        return kwargs
