"""

Defines the relation between units for the Minio Application.

relation data format:
{
    "num_disks": ...,
    "url": ...,
}

Params:
num_disks: defined per unit and cites the number of disks
url: defined per unit and defines the hostname to be used to connect
     several minio units together. Urls must have the format:
     http(s)://<hostname/ip>:<port>
used_folders: folders used for the data of each of the disks. Used to
              construct the MINIO_VOLUMES variable.

"""

from wand.apps.relations.relation_manager_base import RelationManagerBase


class MinioClusterNumDisksMustBeDivisibleBy4(Exception):
    def __init__(self, num_disks):
        super().__init__(
            "Minio Cluster has {} disks, which is "
            "not divisible by 4".format(str(num_disks)))


class MinioClusterManager(RelationManagerBase):

    def __init__(self, charm, relation_name, url,
                 storage_name, min_units=3, min_disks=4):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._unit = charm.unit
        self._min_units = min_units
        self._min_disks = min_disks
        self._url = url
        self._relation_name = relation_name
        self._storage_name = storage_name
        self._used_folders = []

    def set_sans(self, s):
        """Sets the sans to be shared across all units.
        Args:
            s: list of sans (strings) to be passed to ther units
        """
        if not self.relation:
            return
        self.send("sans", ",".join(s))

    def get_sans(self):
        if not self.relation:
            return []
        result = []
        for u in self.relation.units:
            result.extend(
                self.relation.data[u].get("sans", "").split(","))
        return result

    @property
    def minio_volumes(self):
        if not self.relation:
            return ""
        return self.relation.data[self._charm.app].get("minio_volumes", "")

    @property
    def min_units(self):
        return self._min_units

    @property
    def min_disks(self):
        return self._min_disks

    @property
    def url(self):
        return self.relation.data[self._unit]["url"]

    @property
    def used_folders(self):
        return self._used_folders

    @property
    def peers_gone(self):
        if not self.relation:
            # No relation detected, no need to worry about it for now
            return 0
        return int(self.relation.data[self._charm.app].get("peers_gone", 0))

    @property
    def ack_peer_restablished(self):
        if not self.relation:
            return
        return self.relation.data[self._unit].get(
            "ack_peer_restablished", False)

    @ack_peer_restablished.setter
    def ack_peer_restablished(self, ack):
        if not self.relation:
            return
        self.send("ack_peer_restablished", ack)

    @peers_gone.setter
    def peers_gone(self, p):
        if not self.relation:
            return
        self.send_app("peers_gone", p)

    @minio_volumes.setter
    def minio_volumes(self, v):
        if self._charm.unit.is_leader():
            self.send_app("minio_volumes", v)

    @min_units.setter
    def min_units(self, m):
        self._min_units = m

    @min_disks.setter
    def min_disks(self, m):
        self._min_disks = m

    @url.setter
    def url(self, u):
        self._url = u
        self.send("url", self._url)

    @used_folders.setter
    def used_folders(self, f):
        self._used_folders = f
        self.send("used_folders", ",".join(self._used_folders))

    def get_root_pwd(self):
        if not self.relation:
            return ""
        # .get considers the case which cluster is not yet formed
        # return an empty value in this case.
        return self.relation.data[self._charm.app].get("root_pwd", "")

    def set_root_pwd(self, pwd):
        if self._charm.unit.is_leader():
            self.send_app("root_pwd", pwd)

    def is_ready(self):
        if not self.relation:
            return False
        if len(self.relation.units) + 1 < self._min_units:
            return False
        num_disks = len(self._charm.model.storages[self._storage_name])
        for u in self.relation.units:
            num_disks += int(self.relation.data[u].get("num_disks", 0))
        if num_disks < self._min_disks:
            return False
        if num_disks % 4 > 0:
            raise MinioClusterNumDisksMustBeDivisibleBy4(num_disks)
        return True

    def endpoints(self):
        if not self.relation:
            return
        result = {}
        for u in self.relation.units:
            if self.relation.data[u].get("url", None) and \
               self.relation.data[u].get("used_folders", None):
                result[self.relation.data[u]["url"]] = \
                    self.relation.data[u]["used_folders"].split(",")
        return result

    def relation_joined(self, event):
        self.relation_changed(event)

    def relation_changed(self, event):
        # Send current count of disks for this unit
        self.send("num_disks",
                  len(self._charm.model.storages[self._storage_name]))
        self.send("url", self._url)
        self.send("used_folders", ",".join(self._used_folders))
