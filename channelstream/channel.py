import copy
import logging
import six
import uuid

from datetime import datetime

from channelstream import server_state
from channelstream.utils import process_catchup

log = logging.getLogger(__name__)


class Channel(object):
    """ Represents one of our chat channels - has some config options """

    config_keys = [
        "notify_presence",
        "store_history",
        "history_size",
        "broadcast_presence_with_user_lists",
        "notify_state",
        "store_frames",
    ]

    def __init__(self, name, long_name=None, channel_config=None):
        """

        :param name:
        :param long_name:
        :param channel_config:
        """
        self.uuid = uuid.uuid4()
        self.name = name
        self.long_name = long_name
        self.last_active = None
        self.connections = {}
        self.notify_presence = False
        self.broadcast_presence_with_user_lists = False
        # channel sends all user state key changes
        self.notify_state = False
        self.salvageable = False
        self.store_history = False
        self.store_frames = True
        self.history_size = 10
        self.history = []
        # store frames for fetching when connection is established
        # those frames will store channel messages including presence ones
        self.frames = []
        if channel_config:
            self.reconfigure_from_dict(channel_config)
        log.info("%s created" % self)
        log.info("Configuration used: {}".format(channel_config))
        self.mark_activity()

    def mark_activity(self):
        self.last_active = datetime.utcnow()

    def get_catchup_frames(self, newer_than, username):
        found = []
        for t, f in self.frames:
            # either old frame or user is excluded or PM not meant for user
            if (
                t < newer_than
                or (f["exclude_users"] and username in f["exclude_users"])
                or (f["pm_users"] and username not in f["pm_users"])
            ):
                continue

            found.append(process_catchup(f))
        return found

    def reconfigure_from_dict(self, config):
        if config:
            for key in self.config_keys:
                val = config.get(key)
                if val is not None:
                    setattr(self, key, val)

    def add_connection(self, connection):
        username = connection.username
        connections = self.connections.setdefault(username, [])
        if not connections and self.notify_presence:
            self.send_notify_presence_info(username, "joined")
        if connection not in connections:
            connections.append(connection)
            return True
        return False

    def remove_connection(self, connection):
        was_found = False
        username = connection.username
        connections = self.connections.setdefault(username, [])
        if connection in connections:
            self.connections[username].remove(connection)
            was_found = True

        self.after_parted(username)
        return was_found

    def after_parted(self, username):
        """
        Sends parted message if necessary and removed username from
        connections if empty

        :param username:
        :return:
        """
        if not self.connections[username]:
            del self.connections[username]
            if self.notify_presence:
                self.send_notify_presence_info(username, "parted")

    def send_notify_presence_info(self, username, action):
        """
        Sends a message to other connected parties about a presence change
        :param username:
        :param action:
        :return:
        """
        connected_users = []
        if self.broadcast_presence_with_user_lists:
            for _username in self.connections.keys():
                user_inst = server_state.USERS.get(_username)
                user_data = {
                    "user": user_inst.username,
                    "state": user_inst.public_state,
                }
                connected_users.append(user_data)

        self.mark_activity()
        payload = {
            "uuid": uuid.uuid4(),
            "type": "presence",
            "no_history": False,
            "pm_users": [],
            "exclude_users": [username],
            "user": username,
            "users": connected_users,
            "timestamp": self.last_active,
            "channel": self.name,
            "message": {"action": action},
            "state": None,
            "catchup": False,
        }
        if action == "joined":
            payload["state"] = server_state.USERS[username].public_state
        self.add_message(payload, exclude_users=payload["exclude_users"])
        return payload

    def send_user_state(self, user_inst, changed):
        self.mark_activity()

        public_changed = [x for x in changed if x["key"] in user_inst.public_state]

        payload = {
            "uuid": uuid.uuid4(),
            "type": "user_state_change",
            "no_history": False,
            "pm_users": [],
            "exclude_users": [],
            "user": user_inst.username,
            "timestamp": self.last_active,
            "catchup": False,
            "channel": self.name,
            "message": {"state": user_inst.public_state, "changed": public_changed},
        }
        self.add_message(payload)
        return payload

    def add_frame(self, frame):
        if self.store_frames:
            self.frames.append((datetime.utcnow(), frame))
            self.frames = self.frames[-100:]

    def add_to_history(self, message):
        if self.store_history and message["type"] == "message":
            self.history.append(message)
            self.history = self.history[self.history_size * -1 :]

    def add_message(self, message, pm_users=None, exclude_users=None):
        """
        Sends the message to all connections subscribed to this channel
        """
        pm_users = pm_users or []
        exclude_users = exclude_users or []
        self.mark_activity()
        if not message["no_history"]:
            self.add_to_history(message)
        self.add_frame(message)
        message = copy.deepcopy(message)
        # do not leak delivery info
        del message["no_history"]
        del message["pm_users"]
        del message["exclude_users"]
        total_sent = 0
        # message everyone subscribed except excluded
        for user, conns in six.iteritems(self.connections):
            if not exclude_users or user not in exclude_users:
                for connection in conns:
                    if not pm_users or connection.username in pm_users:
                        connection.add_message(message)
                        total_sent += 1
        return total_sent

    def __repr__(self):
        return "<Channel: %s, connections:%s>" % (self.name, len(self.connections))

    def get_info(self, include_history=True, include_users=False):
        settings = {k: getattr(self, k) for k in self.config_keys}

        chan_info = {
            "uuid": self.uuid,
            "name": self.name,
            "long_name": self.long_name,
            "settings": settings,
            "history": self.history if include_history else [],
            "last_active": self.last_active,
            "total_connections": sum(
                [len(conns) for conns in self.connections.values()]
            ),
            "total_users": 0,
            "users": [],
        }

        for username in self.connections.keys():
            user_inst = server_state.USERS.get(username)
            if include_users and user_inst.username not in chan_info["users"]:
                chan_info["users"].append(user_inst.username)
        chan_info["users"] = sorted(chan_info["users"])
        chan_info["total_users"] = len(chan_info["users"])
        return chan_info

    def alter_message(self, to_edit):
        found_history = False
        for msg in self.history:
            if msg["uuid"] == to_edit["uuid"]:
                found_history = True
                msg.update(to_edit)
                altered = copy.deepcopy(msg)
                altered["type"] = "message:edit"
                self.add_message(
                    altered,
                    pm_users=altered["pm_users"],
                    exclude_users=altered["exclude_users"],
                )
                break
        # if found history then reference in frames will be also updated,
        # otherwise search frames for channels that do not store history
        if found_history:
            return

        for f, msg in self.frames:
            if msg["uuid"] == to_edit["uuid"] and msg['type'] == 'message':
                msg.update(to_edit)
                altered = copy.deepcopy(msg)
                altered["type"] = "message:edit"
                self.add_message(
                    altered,
                    pm_users=altered["pm_users"],
                    exclude_users=altered["exclude_users"],
                )
                break

    def delete_message(self, to_delete):
        deleted = None
        for i, msg in enumerate(self.history):
            if msg["uuid"] == to_delete["uuid"]:
                deleted = copy.deepcopy(msg)
                self.history.pop(i)
                deleted["type"] = "message:deleted"
                deleted["no_history"] = True
                break

        for i, frame in enumerate(self.frames):
            msg = frame[1]
            if msg["uuid"] == to_delete["uuid"] and msg['type'] == 'message':
                deleted = copy.deepcopy(msg)
                self.frames.pop(i)
                deleted["type"] = "message:deleted"
                deleted["no_history"] = True
                break

        if deleted:
            self.add_message(
                deleted,
                pm_users=deleted["pm_users"],
                exclude_users=deleted["exclude_users"],
            )

    def __json__(self, request=None):
        return self.get_info()
