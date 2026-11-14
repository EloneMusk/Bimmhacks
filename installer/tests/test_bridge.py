#!/usr/bin/env python3
"""BeeM-Flash wireless bridge deploy (do_beem), against a FAKE ssh - no car, no network.

    python3 installer/tests/test_bridge.py

A stub `ssh` runs each command in a sandbox: it answers the root/MGU connection check, writes the files
the installer streams with `cat >`, and records every command. The tests prove do_beem copies the right
bridge files to the unit and runs the right script - the read-only probes never run a destructive command.
"""
import os
import stat
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'installer'))
import bimmhacks_installer as bi  # noqa: E402

STUB = '''#!/bin/sh
for a; do cmd="$a"; done
cmd=${cmd#export LC_ALL=C; }
printf '%s\\n' "$cmd" >> "$SSH_LOG"
case "$cmd" in
    "echo CONNECTED"*) printf 'CONNECTED\\n0\\nMGU_OAP_OK\\n' ;;
    "mkdir -p "*) d=${cmd#mkdir -p }; mkdir -p "$SANDBOX$d" ;;
    "cat > "*) f=${cmd#cat > }; mkdir -p "$(dirname "$SANDBOX$f")"; cat > "$SANDBOX$f" ;;
    *) printf 'RAN %s\\n' "$cmd" ;;
esac
exit 0
'''


class BridgeDeploy(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='bridge-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sandbox = os.path.join(self.dir, 'unit')
        self.log_file = os.path.join(self.dir, 'ssh.log')
        self.stub = os.path.join(self.dir, 'ssh')
        with open(self.stub, 'w') as f:
            f.write(STUB)
        os.chmod(self.stub, 0o755)
        self.key = os.path.join(self.dir, 'id_rsa')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
        os.chmod(self.key, stat.S_IRUSR | stat.S_IWUSR)
        os.environ['SSH_LOG'] = self.log_file
        os.environ['SANDBOX'] = self.sandbox

    def run_beem(self, action):
        lines = []
        code, out = bi.do_beem(action, self.stub, '127.0.0.1', self.key, log=lines.append)
        cmds = []
        if os.path.isfile(self.log_file):
            with open(self.log_file) as f:
                cmds = f.read().splitlines()
        return code, cmds, lines

    def remote_path(self, subdir, name):
        return self.sandbox + bi.BEEM_REMOTE + '/' + subdir + '/' + name

    def assert_copied(self, subdir, name):
        p = self.remote_path(subdir, name)
        self.assertTrue(os.path.isfile(p), 'did not copy %s to the unit' % name)
        with open(p, 'rb') as a, open(os.path.join(bi.BEEM_DIR, subdir, name), 'rb') as b:
            self.assertEqual(a.read(), b.read(), '%s copied wrong' % name)

    def test_probe_is_read_only_and_copies_the_service_files(self):
        code, cmds, _ = self.run_beem('probe')
        self.assertEqual(code, 0)
        for f in ('beem-bridged.js', 'beem-bridged.service', 'install.sh'):
            self.assert_copied('beem-bridge', f)
        self.assertTrue(any(c.endswith('sh install.sh --probe') for c in cmds), 'did not run the --probe')
        # a probe must not install anything: no bare install.sh, no systemctl, no uninstall
        self.assertFalse(any(c.endswith('sh install.sh') for c in cmds), 'probe must not install')
        self.assertFalse(any('uninstall' in c for c in cmds))

    def test_install_runs_install_sh(self):
        code, cmds, _ = self.run_beem('install')
        self.assertEqual(code, 0)
        self.assert_copied('beem-bridge', 'install.sh')
        self.assertTrue(any(c.endswith('&& sh install.sh') for c in cmds), 'did not run install.sh')
        self.assertFalse(any('--probe' in c for c in cmds))

    def test_uninstall_only_sends_uninstall(self):
        code, cmds, _ = self.run_beem('uninstall')
        self.assertEqual(code, 0)
        self.assert_copied('beem-bridge', 'uninstall.sh')
        self.assertTrue(any(c.endswith('sh uninstall.sh') for c in cmds))
        # it must not push the daemon or the service unit
        self.assertFalse(any(c.endswith('beem-bridged.js') for c in cmds if c.startswith('cat >')))

    def test_car_probe_runs_the_read_only_probe(self):
        code, cmds, _ = self.run_beem('car-probe')
        self.assertEqual(code, 0)
        self.assert_copied('bridge-probes', 'probe-car.sh')
        self.assertTrue(any(c.endswith('sh probe-car.sh') for c in cmds))

    def test_a_connection_check_runs_before_any_deploy(self):
        _, cmds, _ = self.run_beem('probe')
        self.assertTrue(cmds and cmds[0].startswith('echo CONNECTED'), 'must verify root MGU first')

    def test_missing_key_is_refused(self):
        with self.assertRaises(bi.InstallerError):
            bi.do_beem('probe', self.stub, '127.0.0.1', os.path.join(self.dir, 'nope'), log=lambda m: None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
