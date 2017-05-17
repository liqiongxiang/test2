# -*- coding: utf-8 -*-
# Created by zhangzhuo@360.cn on 17/5/10
from . import MQ
from functools import wraps
from uuid import uuid4
from multiprocessing import Process
from gevent.pool import Pool
import gevent.monkey
import os
from termcolor import colored
import sys
import fcntl
import pika
import inspect
import time

gevent.monkey.patch_all()


class micro_server(MQ):
    def __init__(self, name, app=None, channel="center", extype="topic", lock=False):
        super(micro_server, self).__init__(app=app, channel=channel, extype=extype)
        self.name = name
        self.app = app
        self.lock = lock
        self.services = {}
        self.id = str(uuid4())
        self.pro = {}
        self.pid = None
        self.LOCK_PATH = os.path.join(os.path.abspath(os.path.dirname(sys.argv[0])), "{0}.lock".format(self.name))
        self.is_running = False

    def single_instance(self):
        try:
            self.fh = open(self.LOCK_PATH, 'w')
            fcntl.lockf(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except EnvironmentError:
            if self.fh is not None:
                self.is_running = True
            else:
                raise

    def proc(self):
        if self.lock:
            self.single_instance()
        if self.is_running:
            print "server[{0}] is already running on {1} mode.".format(colored(self.name, "green"),
                                                                       colored("single", "red"))
            return
        self.connection = self.connect()
        self.pool = Pool(100)
        for service, fn in self.services.items():
            con = self.__make_consumer(service, fn)
            self.pool.spawn(con, )
        self.pool.join()
        return

    def start(self, n=1, daemon=True):
        for i in range(n):
            pro = Process(target=self.proc)
            pro.daemon = daemon
            pro.start()
            self.pro.setdefault(pro.pid, pro)

    def make_gevent_consumer(self, fn):
        def haha(*args, **kwargs):
            args = list(args[:])
            ch, method, props, body = args[0:4]
            # print repr(body)
            body = self.decode_body(body)
            args[3] = body
            ch.basic_ack(delivery_tag=method.delivery_tag)
            f = self.make_co(fn)
            greend = self.pool.spawn(f, *args, **kwargs)
            # print "cosumer done"
            return greend

        return haha

    def make_co(self, fn):
        def warp(*args, **kwargs):
            # print kwargs
            ch, method, props, body = args[0:4]
            ctx = {"ch": ch, "method": method, "props": props, "body": body}
            dargs, dkwargs = body
            fargs = inspect.getargspec(fn).args
            if "ctx" in fargs:
                dkwargs.update({"__CTX": ctx})
            rtdata = fn(*dargs, **dkwargs)
            ch.basic_publish(exchange='',
                             routing_key=props.reply_to,
                             properties=pika.BasicProperties(correlation_id=props.correlation_id),
                             body=self.encode_body(rtdata))

        return warp

    def __make_consumer(self, service_name, fn):
        def consumer():
            print "server[{2}]        service [{0: ^48}]   @  pid:{1} ".format(self.service_qid(service_name),
                                                                               colored(os.getpid(), "green"),
                                                                               colored(self.name, "green"))
            channel = self.connection.channel()
            channel.basic_qos(prefetch_count=1)
            gfn = self.make_gevent_consumer(fn)
            channel.basic_consume(gfn,
                                  queue=self.service_qid(service_name), no_ack=False)
            channel.start_consuming()
            print colored("---channel-close---", "red")

        return consumer

    def service_qid(self, service_name):
        qid = "{0}.{1}".format(self.name, service_name)
        return qid

    def service(self, service_name, *args, **kwargs):
        def process(fn):
            self.services.setdefault(service_name, fn)
            qid = self.service_qid(service_name)
            self.create_queue(qid, exclusive=False, auto_delete=True, )
            self.join(qid, "{0}.{1}".format(self.name, service_name))

            @wraps(fn)
            def wrapper(*args, **kwargs):
                pass

            return wrapper

        return process

    def rpc(self, service):
        def maker(*args, **kwargs):
            # print "hahahahahah", service
            qid = kwargs.get("qid")
            if qid:
                qid = kwargs.pop("qid")
                return self.push_msg(qid, "", (args, kwargs), to=self.service_qid(service), )
            else:
                qid = "rpc_{0}.{1}.{2}".format(self.name, service, uuid4())
                self.create_queue(qid, exclusive=True, auto_delete=True, )
                self.push_msg(qid, "", (args, kwargs), to=self.service_qid(service), )
                while 1:
                    ctx = self.pull_msg(qid=qid)
                    if not ctx:
                        time.sleep(1)
                        continue
                    else:
                        return ctx[-1][-1]

        return maker

    def __getattr__(self, item):
        try:
            r = self.create_queue(self.service_qid(item), passive=True)
        except:
            return super(micro_server, self).__getattribute__(item)
        if r:
            return self.rpc(item)
        else:
            super(micro_server, self).__getattribute__(item)


def main():
    pass


if __name__ == '__main__':
    main()