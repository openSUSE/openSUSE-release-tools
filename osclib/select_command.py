from osclib.stagingapi import StagingAPI
from osclib.request_finder import RequestFinder

class SelectCommand:
    
    def __init__(self, api):
        self.api = api

    def select_request(self, rq, rq_prj):
        if 'staging' not in rq_prj:
            # Normal 'select' command
            self.api.rq_to_prj(rq, self.tprj)
        elif 'staging' in rq_prj and opts.move:
            # 'select' command becomes a 'move'
            fprj = None
            if opts.from_:
                fprj = self.api.prj_from_letter(opts.from_)
            else:
                fprj = rq_prj['staging']
            print('Moving "{}" from "{}" to "{}"'.format(rq, fprj, self.tprj))
            self.api.move_between_project(fprj, rq, self.tprj)
        elif 'staging' in rq_prj and not opts.move:
            # Previously selected, but not explicit move
            msg = 'Request {} is actually in "{}".\n'
            msg = msg.format(rq, rq_prj['staging'])
            msg += 'Use --move modifier to move the request from "{}" to "{}"'
            msg = msg.format(rq_prj['staging'], self.tprj)
            print(msg)
        else:
            raise oscerr.WrongArgs('Arguments for select are not correct.')


    def perform(self, tprj, requests):
        if not self.api.prj_frozen_enough(tprj):
            print('Freeze the prj first')
            return False
        self.tprj = tprj

        for rq, rq_prj in RequestFinder.find_sr(requests, self.api.apiurl).items():
            self.select_request(rq, rq_prj)
