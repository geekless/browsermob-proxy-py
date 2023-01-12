import os
import platform
import signal
import socket
import subprocess
import time
import weakref

import sys

from .client import Client
from .exceptions import ProxyServerError


class RemoteServer(object):

    def __init__(self, host, port):
        """
        Initialises a RemoteServer object

        :param host: The host of the proxy server.
        :param port: The port of the proxy server.
        """
        self.host = host
        self.port = port

    @property
    def url(self):
        """
        Gets the url that the proxy is running on. This is not the URL clients
        should connect to.
        """
        return "http://%s:%d" % (self.host, self.port)

    def create_proxy(self, params=None):
        """
        Gets a client class that allow to set all the proxy details that you
        may need to.

        :param dict params: Dictionary where you can specify params
            like httpProxy and httpsProxy
        """
        params = params if params is not None else {}
        client = Client(self.url[7:], params)
        return client

    def _is_listening(self):
        try:
            socket_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socket_.settimeout(1)
            socket_.connect((self.host, self.port))
            socket_.close()
            return True
        except socket.error:
            return False


class Server(RemoteServer):

    # Wrapper for proper finalization
    class __FinalizedServer(object):
        def __init__(self, server):
            self.win_env = sys.platform == "win32"
            self.process = None
            self.log_file = None
            weakref.finalize(server, self.stop)

        def stop(self):
            if self.process and self.process.poll() is None:
                group_pid = os.getpgid(self.process.pid) if not self.win_env else self.process.pid
                try:
                    self.process.kill()
                    self.process.wait()
                    os.killpg(group_pid, signal.SIGINT)
                    self.process = None
                except AttributeError:
                    # kill may not be available under windows environment
                    pass

            if self.log_file:
                self.log_file.close()
                self.log_file = None

    def __init__(self, path='browsermob-proxy', options=None):
        """
        Initialises a Server object

        :param str path: Path to the browsermob proxy batch file
        :param dict options: Dictionary that can hold the port.
            More items will be added in the future.
            This defaults to an empty dictionary
        """
        options = options if options is not None else {}

        path_var_sep = ':'
        if platform.system() == 'Windows':
            path_var_sep = ';'
            if not path.endswith('.bat'):
                path += '.bat'

        exec_not_on_path = True
        for directory in os.environ['PATH'].split(path_var_sep):
            if(os.path.isfile(os.path.join(directory, path))):
                exec_not_on_path = False
                break

        if not os.path.isfile(path) and exec_not_on_path:
            raise ProxyServerError("Browsermob-Proxy binary couldn't be found "
                                   "in path provided: %s" % path)

        self.path = path
        self.host = options.get('host', 'localhost')
        self.port = options.get('port', 8080)
        self.proxyPortRange = options.get('proxyPortRange', '8081-8581')

        self.fin = Server.__FinalizedServer(self)

        if platform.system() == 'Darwin':
            self.command = ['sh']
        else:
            self.command = []
        self.command += [path, '--port=%s' % self.port, '--proxyPortRange=%s' % self.proxyPortRange]

    def start(self, options=None):
        """
        This will start the browsermob proxy and then wait until it can
        interact with it

        :param dict options: Dictionary that can hold the path and filename
            of the log file with resp. keys of `log_path` and `log_file`
        """
        if options is None:
            options = {}
        log_path = options.get('log_path', os.getcwd())
        log_file = options.get('log_file', 'server.log')
        retry_sleep = options.get('retry_sleep', 0.5)
        retry_count = options.get('retry_count', 60)
        log_path_name = os.path.join(log_path, log_file)
        self.fin.log_file = open(log_path_name, 'w')

        if self._is_listening():
            raise ProxyServerError("Port already in use: %s" % self.port)

        if self.fin.win_env:
            self.fin.process = self._start_on_windows()
        else:
            self.fin.process = self._start_on_unix()

        count = 0
        while not self._is_listening():
            # FIXME: race condition!
            # The code should detect the proxy failed to start, but it doesn't.
            # BrowserMob Proxy (v2.1.4 at least) just hangs when cann't bind to the port,
            # so self.fin.process.poll() never return True.
            # We are not able to detect the issue if another process binds to the port
            # while the proxy process is starting up.
            if self.fin.process.poll():
                message = (
                    "The Browsermob-Proxy server process failed to start. "
                    "Check {0}"
                    "for a helpful error message.".format(self.fin.log_file))

                raise ProxyServerError(message)
            time.sleep(retry_sleep)
            count += 1
            if count == retry_count:
                self.stop()
                raise ProxyServerError("Can't connect to Browsermob-Proxy")

    def _start_on_windows(self):
        return subprocess.Popen(self.command,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                stdout=self.fin.log_file,
                                stderr=subprocess.STDOUT)

    def _start_on_unix(self):
        return subprocess.Popen(self.command,
                                preexec_fn=os.setsid,
                                stdout=self.fin.log_file,
                                stderr=subprocess.STDOUT)

    def stop(self):
        """
        This will stop the process running the proxy
        """
        self.fin.stop()
