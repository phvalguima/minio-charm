from wand.apps.relations.base_prometheus_monitoring import (
    BasePrometheusMonitor
)


class PrometheusMonitorCluster(BasePrometheusMonitor):

    def request(self, port, metrics_path='/minio/v2/metrics/',
                endpoint=None, ca_cert=None):
        """Request registers the Prometheus scrape job.
        port: to be used as part of the target
        """
        name = "{}_cluster".format(self._charm.app.name)
        # advertise_addr given that minio endpoint uses advertise_addr
        # to find its hostname
        job = {
            'job_name': name,
            'job_data': {
                'static_configs': [{
                    'targets': ["{}:{}".format(
                        endpoint or self.advertise_addr, port)]
                }],
                'scheme': 'http',
                'metrics_path': metrics_path + "cluster"
            }
        }
        if ca_cert:
            job['tls_config'] = {'ca_file': '__ca_file__'}
            job['scheme'] = 'https'
            super().request(name, ca_cert=ca_cert,
                            job_data=job)
            return
        super().request(name, job_data=job)


class PrometheusMonitorNode(BasePrometheusMonitor):

    def request(self, port, metrics_path='/minio/v2/metrics/',
                endpoint=None, ca_cert=None):
        """Request registers the Prometheus scrape job.
        port: to be used as part of the target
        """
        # advertise_addr given that minio endpoint uses advertise_addr
        # to find its hostname
        name = "{}_node".format(self._charm.unit.name.replace("/", "-"))
        data = {
            'job_name': name,
            'job_data': {
                'static_configs': [{
                    'targets': ["{}:{}".format(
                        endpoint or self.advertise_addr, port)]
                }],
                'scheme': 'http',
                'metrics_path': metrics_path + "node"
            }
        }
        if ca_cert:
            data['tls_config'] = {'ca_file': '__ca_file__'}
            data['scheme'] = 'https'
            super().request(name, ca_cert=ca_cert, job_data=data)
            return
        super().request(name, job_data=data)
