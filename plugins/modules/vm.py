#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026, community.vagrant contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type


DOCUMENTATION = r"""
---
module: vm
short_description: Manage a Vagrant VM with python-vagrant
description:
  - Manage a single Vagrant VM using the
    U(https://github.com/pycontribs/python-vagrant) library.
  - The module renders an authoritative Vagrantfile in a module-managed working
    directory and then converges the requested VM lifecycle state.
version_added: "0.1.0"
author:
  - Daniel Brennand (@dbrennand)
requirements:
  - vagrant
  - python-vagrant
options:
  state:
    description:
      - Desired VM lifecycle state.
      - C(present) only ensures the workdir and Vagrantfile exist.
      - C(started) ensures the VM is running.
      - C(stopped) halts the VM if it exists.
      - C(restarted) always reloads an existing VM, or starts it if absent.
      - C(provisioned) starts the VM if needed and always runs provisioning.
      - C(absent) destroys the VM and optionally removes the workdir.
    type: str
    required: true
    choices:
      - present
      - started
      - stopped
      - restarted
      - provisioned
      - absent
  name:
    description:
      - Name of the VM managed by this task.
      - The name is also used to derive the default workdir path.
    type: str
    required: true
  workdir:
    description:
      - Directory containing the generated Vagrantfile and Vagrant metadata.
      - Defaults to a stable cache path under the remote user's home directory.
    type: path
  cleanup_workdir:
    description:
      - Whether to delete the workdir after C(state=absent).
      - Only module-managed workdirs under the community.vagrant cache
        directory are eligible for deletion.
    type: bool
    default: true
  box:
    description:
      - Vagrant box name for the VM.
      - Required unless C(state=absent).
    type: str
  box_version:
    description:
      - Optional Vagrant box version.
    type: str
  box_url:
    description:
      - Optional URL used to source the Vagrant box.
    type: str
  hostname:
    description:
      - Guest hostname configured in the generated Vagrantfile.
    type: str
  provider:
    description:
      - Vagrant provider to use when starting the VM.
    type: str
  provider_settings:
    description:
      - Provider-specific configuration rendered inside the provider block.
      - Top-level keys are rendered as provider attribute assignments.
      - Values may be scalars, lists, or nested mappings, which are converted to
        Ruby literals.
    type: dict
    default: {}
  forwarded_ports:
    description:
      - Forwarded port definitions rendered with C(vm.network "forwarded_port").
    type: list
    elements: dict
    default: []
    suboptions:
      guest:
        description:
          - Guest port to expose from the VM.
        type: int
        required: true
      host:
        description:
          - Host port bound to the forwarded guest port.
        type: int
        required: true
      id:
        description:
          - Optional forwarded port identifier.
        type: str
      protocol:
        description:
          - Optional protocol name such as C(tcp) or C(udp).
        type: str
      host_ip:
        description:
          - Optional host interface address to bind the forwarded port to.
        type: str
      auto_correct:
        description:
          - Whether Vagrant may automatically correct host port collisions.
        type: bool
  synced_folders:
    description:
      - Synced folder definitions rendered with C(vm.synced_folder).
    type: list
    elements: dict
    default: []
    suboptions:
      src:
        description:
          - Source path on the host system.
        type: path
        required: true
      dest:
        description:
          - Destination path inside the guest VM.
        type: path
        required: true
      type:
        description:
          - Optional synced folder implementation such as C(rsync).
        type: str
      owner:
        description:
          - Optional guest ownership user for the mounted path.
        type: str
      group:
        description:
          - Optional guest ownership group for the mounted path.
        type: str
      mount_options:
        description:
          - Optional mount options passed to the synced folder implementation.
        type: list
        elements: str
      create:
        description:
          - Whether the host source path should be created if it does not exist.
        type: bool
      disabled:
        description:
          - Whether the synced folder entry is disabled.
        type: bool
  environment:
    description:
      - Environment variables passed to the Vagrant subprocess.
    type: dict
    default: {}
"""

EXAMPLES = r"""
- name: Render a Vagrant environment without starting the VM
  community.vagrant.vm:
    state: present
    name: web
    box: bento/ubuntu-22.04
    provider: virtualbox
    provider_settings:
      memory: 2048
      cpus: 2
    forwarded_ports:
      - guest: 80
        host: 8080

- name: Start a VM and return SSH facts
  community.vagrant.vm:
    state: started
    name: web
    box: bento/ubuntu-22.04
    hostname: web.test
    synced_folders:
      - src: /srv/project
        dest: /vagrant
        type: rsync

- name: Destroy a VM and remove its workdir
  community.vagrant.vm:
    state: absent
    name: web
    cleanup_workdir: true
"""

RETURN = r"""
changed:
  description: Whether the module changed the system.
  returned: always
  type: bool
state:
  description:
    - Effective VM state after the requested action.
    - In check mode, this is the predicted post-action VM state.
  returned: always
  type: str
name:
  description: Managed VM name.
  returned: always
  type: str
workdir:
  description: Effective Vagrant working directory.
  returned: always
  type: str
status:
  description: Current Vagrant machine status when available.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the managed machine reported by Vagrant.
      type: str
    state:
      description:
        - Current Vagrant machine state.
        - In check mode, this remains the current pre-action Vagrant state.
      type: str
    provider:
      description: Provider name reported by Vagrant when available.
      type: str
ssh:
  description: SSH connection facts when the VM is available.
  returned: when available
  type: dict
  contains:
    hostname:
      description: SSH hostname reported by Vagrant.
      type: str
    port:
      description: SSH port reported by Vagrant.
      type: str
    user:
      description: SSH username reported by Vagrant.
      type: str
    keyfile:
      description: SSH private key path reported by Vagrant.
      type: str
"""

import os
import re
import shutil
import stat
import tempfile
from copy import deepcopy

from ansible.module_utils.basic import AnsibleModule


STATE_NOT_CREATED = "not_created"
STATE_RUNNING = "running"
STOPPED_STATES = frozenset(("poweroff", "saved", "aborted", "stopped", "shutoff"))
VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
VAGRANTFILE_NAME = "Vagrantfile"
DISALLOWED_RUBY_STRING_CHARS = ("\0", "\n", "\r", "\t")


def managed_workdir_root():
    home = os.environ.get("HOME")
    if not home:
        home = os.path.abspath(os.sep)
    return os.path.join(home, ".cache", "ansible", "community.vagrant")


def default_workdir(name):
    return os.path.join(managed_workdir_root(), name)


def normalize_state(state):
    if state is None:
        return None
    return str(state).strip().lower().replace(" ", "_")


def disallowed_ruby_string_char(value):
    for candidate in DISALLOWED_RUBY_STRING_CHARS:
        if candidate in value:
            return candidate
    return None


def ruby_string(value):
    if disallowed_ruby_string_char(value) is not None:
        raise ValueError(
            "string values rendered into the Vagrantfile must not contain NUL, newline, carriage return, or tab characters"
        )
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return "'{0}'".format(escaped)


def ruby_key(key):
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        return "{0}:".format(key)
    return "{0} =>".format(ruby_string(key))


def ruby_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nil"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return ruby_string(value)
    if isinstance(value, list):
        return "[{0}]".format(", ".join(ruby_literal(item) for item in value))
    if isinstance(value, dict):
        return "{{{0}}}".format(", ".join("{0} {1}".format(ruby_key(str(key)), ruby_literal(val)) for key, val in value.items()))
    raise TypeError("Unsupported Ruby literal value: {0!r}".format(value))


def ruby_options(mapping):
    return ", ".join("{0} {1}".format(ruby_key(str(key)), ruby_literal(value)) for key, value in mapping.items())


def build_vagrantfile(params):
    lines = [
        '# Generated by community.vagrant.vm. Local edits will be replaced.',
        'Vagrant.configure("2") do |config|',
        '  config.vm.define {0} do |machine|'.format(ruby_string(params["name"])),
        '    machine.vm.box = {0}'.format(ruby_string(params["box"])),
    ]

    if params.get("box_version"):
        lines.append('    machine.vm.box_version = {0}'.format(ruby_string(params["box_version"])))
    if params.get("box_url"):
        lines.append('    machine.vm.box_url = {0}'.format(ruby_string(params["box_url"])))
    if params.get("hostname"):
        lines.append('    machine.vm.hostname = {0}'.format(ruby_string(params["hostname"])))

    for port in params.get("forwarded_ports") or []:
        options = {
            "guest": port["guest"],
            "host": port["host"],
        }
        for optional_key in ("id", "protocol", "host_ip", "auto_correct"):
            if port.get(optional_key) is not None:
                options[optional_key] = port[optional_key]
        lines.append('    machine.vm.network "forwarded_port", {0}'.format(ruby_options(options)))

    for folder in params.get("synced_folders") or []:
        extra = {}
        for optional_key in ("type", "owner", "group", "mount_options", "create", "disabled"):
            if folder.get(optional_key) is not None:
                extra[optional_key] = folder[optional_key]
        if extra:
            lines.append(
                "    machine.vm.synced_folder {0}, {1}, {2}".format(
                    ruby_string(folder["src"]),
                    ruby_string(folder["dest"]),
                    ruby_options(extra),
                )
            )
        else:
            lines.append(
                "    machine.vm.synced_folder {0}, {1}".format(
                    ruby_string(folder["src"]),
                    ruby_string(folder["dest"]),
                )
            )

    provider = params.get("provider")
    provider_settings = params.get("provider_settings") or {}
    if provider or provider_settings:
        provider_name = provider or "default"
        lines.append("    machine.vm.provider {0} do |provider_config|".format(ruby_string(provider_name)))
        for key, value in provider_settings.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                raise ValueError("provider_settings keys must be valid Ruby identifiers: {0}".format(key))
            lines.append("      provider_config.{0} = {1}".format(key, ruby_literal(value)))
        lines.append("    end")

    lines.extend(
        [
            "  end",
            "end",
            "",
        ]
    )
    return "\n".join(lines)


def lstat_path(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def common_path(path_a, path_b):
    try:
        return os.path.commonpath((path_a, path_b))
    except ValueError:
        return None


def workdir_cleanup_allowed(path):
    managed_root = os.path.realpath(managed_workdir_root())
    resolved = os.path.realpath(path)
    if resolved in (os.path.realpath(os.sep), managed_root):
        return False
    if common_path(managed_root, resolved) != managed_root:
        return False
    metadata = lstat_path(path)
    if metadata is not None and stat.S_ISLNK(metadata.st_mode):
        return False
    if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
        return False
    return True


def ensure_safe_workdir(path):
    metadata = lstat_path(path)
    if metadata is None:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("workdir must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("workdir must be a directory path")
    return metadata


def read_text(path):
    metadata = lstat_path(path)
    if metadata is None:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Vagrantfile must be a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Failed to open Vagrantfile safely: {0}".format(exc))

    with os.fdopen(file_descriptor, "r", encoding="utf-8") as file_handle:
        metadata = os.fstat(file_handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Vagrantfile must be a regular file")
        return file_handle.read()


def ensure_directory(path, check_mode):
    if ensure_safe_workdir(path) is not None:
        return False
    if check_mode:
        return True
    os.makedirs(path)
    return True


def ensure_vagrantfile(path, content, check_mode):
    current = read_text(path)
    changed = current != content
    if changed and not check_mode:
        file_handle = None
        temporary_path = None
        try:
            file_descriptor, temporary_path = tempfile.mkstemp(prefix=".community-vagrant.", dir=os.path.dirname(path))
            file_handle = os.fdopen(file_descriptor, "w", encoding="utf-8")
            file_handle.write(content)
            file_handle.close()
            file_handle = None
            os.replace(temporary_path, path)
        finally:
            if file_handle is not None:
                file_handle.close()
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)
    return changed


def safe_rmtree(path):
    if ensure_safe_workdir(path) is not None:
        shutil.rmtree(path)
        return True
    return False


def get_vagrant_status(client, name):
    statuses = client.status(vm_name=name)
    if not statuses:
        return {
            "name": name,
            "state": STATE_NOT_CREATED,
            "provider": None,
        }

    status = statuses[0]
    return {
        "name": getattr(status, "name", name),
        "state": normalize_state(getattr(status, "state", STATE_NOT_CREATED)),
        "provider": getattr(status, "provider", None),
    }


def vm_exists(status):
    return status.get("state") not in (None, STATE_NOT_CREATED)


def vm_running(status):
    return status.get("state") == STATE_RUNNING


def collect_ssh_facts(client, name):
    try:
        return {
            "hostname": client.hostname(vm_name=name),
            "port": client.port(vm_name=name),
            "user": client.user(vm_name=name),
            "keyfile": client.keyfile(vm_name=name),
        }
    except Exception:
        return None


def predict_changed(state, config_changed, status, cleanup_workdir, workdir_exists):
    if state == "present":
        return config_changed
    if state == "started":
        return config_changed or not vm_running(status)
    if state == "stopped":
        return config_changed or vm_exists(status) and status.get("state") not in STOPPED_STATES
    if state == "restarted":
        return True
    if state == "provisioned":
        return True
    if state == "absent":
        return vm_exists(status) or cleanup_workdir and workdir_exists
    return False


def predict_effective_state(state, status):
    current_state = normalize_state(status.get("state")) or STATE_NOT_CREATED

    if state == "present":
        return current_state
    if state == "started":
        return STATE_RUNNING
    if state == "stopped":
        if current_state in STOPPED_STATES or current_state == STATE_NOT_CREATED:
            return current_state
        return "poweroff"
    if state == "restarted":
        return STATE_RUNNING
    if state == "provisioned":
        return STATE_RUNNING
    if state == "absent":
        return STATE_NOT_CREATED
    return current_state


def validate_params(module, params):
    state = params["state"]
    name = params["name"]

    if not VALID_NAME.match(name):
        module.fail_json(msg="name must contain only letters, numbers, dot, underscore, or hyphen")

    if state != "absent" and not params.get("box"):
        module.fail_json(msg="box is required unless state=absent")

    if params.get("provider_settings") and not params.get("provider"):
        module.fail_json(msg="provider is required when provider_settings is set")

    try:
        ensure_safe_workdir(params["workdir"])
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    if state == "absent" and params["cleanup_workdir"] and not workdir_cleanup_allowed(params["workdir"]):
        module.fail_json(msg="cleanup_workdir=true only supports module-managed workdirs under the community.vagrant cache directory")

    for key, value in (params.get("environment") or {}).items():
        if not isinstance(key, str):
            module.fail_json(msg="environment keys must be strings")
        if not isinstance(value, str):
            module.fail_json(msg="environment values must be strings")


def run_module():
    module = AnsibleModule(
        argument_spec={
            "state": {
                "type": "str",
                "required": True,
                "choices": ["present", "started", "stopped", "restarted", "provisioned", "absent"],
            },
            "name": {"type": "str", "required": True},
            "workdir": {"type": "path"},
            "cleanup_workdir": {"type": "bool", "default": True},
            "box": {"type": "str"},
            "box_version": {"type": "str"},
            "box_url": {"type": "str"},
            "hostname": {"type": "str"},
            "provider": {"type": "str"},
            "provider_settings": {"type": "dict", "default": {}},
            "forwarded_ports": {
                "type": "list",
                "elements": "dict",
                "default": [],
                "options": {
                    "guest": {"type": "int", "required": True},
                    "host": {"type": "int", "required": True},
                    "id": {"type": "str"},
                    "protocol": {"type": "str"},
                    "host_ip": {"type": "str"},
                    "auto_correct": {"type": "bool"},
                },
            },
            "synced_folders": {
                "type": "list",
                "elements": "dict",
                "default": [],
                "options": {
                    "src": {"type": "path", "required": True},
                    "dest": {"type": "path", "required": True},
                    "type": {"type": "str"},
                    "owner": {"type": "str"},
                    "group": {"type": "str"},
                    "mount_options": {"type": "list", "elements": "str"},
                    "create": {"type": "bool"},
                    "disabled": {"type": "bool"},
                },
            },
            "environment": {"type": "dict", "default": {}},
        },
        supports_check_mode=True,
    )

    params = deepcopy(module.params)
    params["workdir"] = os.path.abspath(params["workdir"] or default_workdir(params["name"]))
    validate_params(module, params)

    result = {
        "changed": False,
        "name": params["name"],
        "state": params["state"],
        "workdir": params["workdir"],
        "status": {
            "name": params["name"],
            "state": STATE_NOT_CREATED,
            "provider": params.get("provider"),
        },
    }

    vagrantfile_content = None
    if params["state"] != "absent":
        try:
            vagrantfile_content = build_vagrantfile(params)
        except (TypeError, ValueError) as exc:
            module.fail_json(msg=str(exc), **result)

    workdir = params["workdir"]
    vagrantfile_path = os.path.join(workdir, VAGRANTFILE_NAME)
    workdir_exists = os.path.exists(workdir)
    config_changed = False

    if params["state"] != "absent":
        try:
            result["changed"] |= ensure_directory(workdir, module.check_mode)
            config_changed = ensure_vagrantfile(vagrantfile_path, vagrantfile_content, module.check_mode)
            result["changed"] |= config_changed
        except (OSError, ValueError) as exc:
            module.fail_json(msg="Failed to prepare Vagrant workdir: {0}".format(exc), **result)

    client = None
    status = result["status"]
    can_query_vm = os.path.isfile(vagrantfile_path) or (params["state"] == "absent" and os.path.isdir(workdir))

    if can_query_vm:
        try:
            import vagrant as vagrant_lib
        except ImportError:
            module.fail_json(msg="python-vagrant is required for this module", **result)

        try:
            client = vagrant_lib.Vagrant(root=workdir, quiet_stdout=True, quiet_stderr=True, env=params["environment"] or None)
            status = get_vagrant_status(client, params["name"])
        except Exception as exc:
            if params["state"] != "absent":
                module.fail_json(msg="Failed to query Vagrant status: {0}".format(exc), **result)
        result["status"] = status

    if module.check_mode:
        result["changed"] = predict_changed(
            params["state"],
            config_changed or result["changed"],
            status,
            params["cleanup_workdir"],
            workdir_exists,
        )
        result["state"] = predict_effective_state(params["state"], status)
        if params["state"] in ("started", "restarted", "provisioned") and status.get("state") != STATE_NOT_CREATED:
            ssh = collect_ssh_facts(client, params["name"]) if client else None
            if ssh:
                result["ssh"] = ssh
        module.exit_json(**result)

    if client is None and params["state"] != "absent":
        try:
            import vagrant as vagrant_lib
        except ImportError:
            module.fail_json(msg="python-vagrant is required for this module", **result)
        try:
            client = vagrant_lib.Vagrant(root=workdir, quiet_stdout=True, quiet_stderr=True, env=params["environment"] or None)
            status = get_vagrant_status(client, params["name"])
            result["status"] = status
        except Exception as exc:
            module.fail_json(msg="Failed to initialize Vagrant client: {0}".format(exc), **result)

    state = params["state"]

    try:
        if state == "started":
            if config_changed and vm_exists(status):
                client.reload(vm_name=params["name"])
                result["changed"] = True
            elif not vm_running(status):
                client.up(vm_name=params["name"], provider=params.get("provider"))
                result["changed"] = True
        elif state == "stopped":
            if vm_exists(status) and status.get("state") not in STOPPED_STATES:
                client.halt(vm_name=params["name"])
                result["changed"] = True
        elif state == "restarted":
            if vm_exists(status):
                client.reload(vm_name=params["name"])
            else:
                client.up(vm_name=params["name"], provider=params.get("provider"))
            result["changed"] = True
        elif state == "provisioned":
            if not vm_running(status):
                client.up(vm_name=params["name"], provider=params.get("provider"))
                result["changed"] = True
            client.provision(vm_name=params["name"])
            result["changed"] = True
        elif state == "absent":
            if client is not None and vm_exists(status):
                client.destroy(vm_name=params["name"])
                result["changed"] = True
            if params["cleanup_workdir"] and safe_rmtree(workdir):
                result["changed"] = True
            result["status"] = {
                "name": params["name"],
                "state": STATE_NOT_CREATED,
                "provider": params.get("provider"),
            }
    except Exception as exc:
        module.fail_json(msg="Failed to converge VM state: {0}".format(exc), **result)

    if os.path.isfile(vagrantfile_path) and state != "absent":
        try:
            status = get_vagrant_status(client, params["name"])
            result["status"] = status
        except Exception:
            pass

    ssh = None
    if client is not None and result["status"].get("state") == STATE_RUNNING:
        ssh = collect_ssh_facts(client, params["name"])
    if ssh:
        result["ssh"] = ssh

    result["state"] = result["status"].get("state") or state
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
