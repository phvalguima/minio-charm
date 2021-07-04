from wand.apps.relations.relation_manager_base import RelationManagerBase


class BasePrometheusMonitor(RelationManagerBase):

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)

    def request(self, job_name, ca_cert=None, job_data=None):
        # Job name as field and value the json describing it
        self.send(job_name, job_data)


class PrometheusMonitorCluster(BasePrometheusMonitor):

    def request(self, port, metrics_path='/minio/v2/metrics/',
                endpoint=None, ca_cert=None):
        """Request registers the Prometheus scrape job.
        port: to be used as part of the target
        """
        # advertise_addr given that minio endpoint uses advertise_addr
        # to find its hostname
        data = {
            'static_configs': [{
                'targets': ["{}:{}".format(
                    endpoint or self.advertise_addr, port)]
            }],
            'scheme': 'http',
            'metrics_path': metrics_path + "cluster"
        }
        name = "{}_cluster".format(self._charm.app)
        if ca_cert:
            data['tls_config'] = {'ca_file': '__ca_file__'}
            data['scheme'] = 'https'
            super().request(name, ca_cert=ca_cert,
                            job_data=data)
            return
        super().request(name, job_data=data)


class PrometheusMonitorNode(BasePrometheusMonitor):

    def request(self, port, metrics_path='/minio/v2/metrics/',
                endpoint=None, ca_cert=None):
        """Request registers the Prometheus scrape job.
        port: to be used as part of the target
        """
        # advertise_addr given that minio endpoint uses advertise_addr
        # to find its hostname
        data = {
            'static_configs': [{
                'targets': ["{}:{}".format(
                    endpoint or self.advertise_addr, port)]
            }],
            'scheme': 'http',
            'metrics_path': metrics_path + "node"
        }
        name = "{}_node".format(self._charm.unit.replace("/", "-"))
        if ca_cert:
            data['tls_config'] = {'ca_file': '__ca_file__'}
            data['scheme'] = 'https'
            super().request(name, ca_cert=ca_cert, job_data=data)
            return
        super().request(name, job_data=data)
