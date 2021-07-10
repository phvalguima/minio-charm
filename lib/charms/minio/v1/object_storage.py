from ops.framework import Object, StoredState
from wand.apps.relations.relation_manager_base import RelationManagerBase

from charmhelpers.contrib.network.ip import get_hostname


class ObjectStorageRelationManager(RelationManagerBase):

    state = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)

        self._charm = charm
        self._unit = charm.unit
        self._relation_name = relation_name


class ObjectStorageRelationProvider(ObjectStorageRelationManager):

    def __init__(self, charm, relation_name, hostname=None):
        super().__init__(charm, relation_name)
        self.state.set_default(hostname=hostname)
    
    @property
    def hostname(self):
        if self.state.hostname:
            return self.state.hostname
        return get_hostname(self.advertise_addr)
    
    @hostname.setter
    def hostname(self, h):
        self.state.hostname = h

    def send_info(self,
                  access_key,
                  namespace,
                  port,
                  secret_key,
                  secure,
                  service):
        if self.relations:
            self.send(
                {
                    "access-key": access_key,
                    "namespace": namespace,
                    "port": port,
                    "secret-key": secret_key,
                    "secure": secure,
                    "service": service,
                }
            )