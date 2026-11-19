"""Launch the isolated test VM in WSL/KVM; no UI input is sent by this script."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT = Path('/mnt/d/readmaster')
WORK = ROOT / '.build/clean-vm'
RUNTIME = Path('/tmp/readmaster-clean-vm-20261118')
STATE = Path('/var/tmp/readmaster-clean-vm-20261118')
QMP = RUNTIME / 'qmp.sock'


def qmp(command, arguments=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(QMP))
        stream = connection.makefile('rwb')
        json.loads(stream.readline())
        for name, args in [('qmp_capabilities', None), (command, arguments)]:
            request = {'execute': name}
            if args is not None:
                request['arguments'] = args
            stream.write(json.dumps(request).encode() + b'\n')
            stream.flush()
            while True:
                answer = json.loads(stream.readline())
                if 'error' in answer:
                    raise RuntimeError(answer)
                if 'return' in answer:
                    break
        return answer['return']


def prepare_disk():
    assert WORK.resolve().is_relative_to(ROOT.resolve())
    assert STATE.resolve() == Path('/var/tmp/readmaster-clean-vm-20261118')
    STATE.mkdir(mode=0o700, exist_ok=True)
    disk = STATE / 'windows-clean.qcow2'
    if not disk.exists():
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '96G'], check=True)
    variables = STATE / 'OVMF_VARS_4M.ms.fd'
    if not variables.exists():
        shutil.copy2('/usr/share/OVMF/OVMF_VARS_4M.ms.fd', variables)
    return disk, variables


def media():
    subprocess.run(['xorriso', '-as', 'mkisofs', '-iso-level', '3', '-J', '-joliet-long', '-R',
                    '-V', 'RM_VM_TEST', '-o', str(WORK / 'payload.iso'), '-graft-points',
                    '/=' + str(WORK / 'media'),
                    '/release=' + str(ROOT / 'output/ReadMaster-0.5.0.1')], check=True)


def launch(firmware_only=False, hold_seconds=5, accelerator='kvm', resume_checks=False):
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    if QMP.exists():
        try:
            qmp('query-status')
        except (OSError, ValueError):
            QMP.unlink()
        else:
            raise RuntimeError('A test VM is already running')
    disk, variables = prepare_disk()
    tpm_dir = STATE / 'tpm-state'
    tpm_dir.mkdir(exist_ok=True)
    tpm_socket = RUNTIME / 'swtpm.sock'
    if tpm_socket.exists():
        raise RuntimeError('TPM socket already exists; inspect the previous test before reusing state')
    tpm_log = (WORK / 'swtpm.log').open('ab')
    tpm = subprocess.Popen(['swtpm', 'socket', '--tpm2', '--tpmstate', 'dir=' + str(tpm_dir),
                            '--ctrl', 'type=unixio,path=' + str(tpm_socket), '--terminate'],
                           stdout=tpm_log, stderr=tpm_log)
    for _ in range(100):
        if tpm_socket.exists():
            break
        assert tpm.poll() is None, 'swtpm failed to start'
        time.sleep(.1)
    cpu = 'host,vmx=off,pmu=off' if accelerator == 'kvm' else 'max'
    args = ['qemu-system-x86_64', '-name', 'ReadMaster Clean Windows Test',
            '-accel', accelerator, '-machine', 'q35,smm=on', '-cpu', cpu,
            '-smp', '6', '-m', '12288',
            '-global', 'driver=cfi.pflash01,property=secure,value=on',
            '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd',
            '-drive', 'if=pflash,format=raw,unit=1,file=' + str(variables),
            '-chardev', 'socket,id=chrtpm,path=' + str(tpm_socket),
            '-tpmdev', 'emulator,id=tpm0,chardev=chrtpm', '-device', 'tpm-tis,tpmdev=tpm0',
            '-drive', 'file=' + str(disk) + ',format=qcow2,if=none,id=osdisk,cache=writeback',
            '-device', 'ide-hd,drive=osdisk,bus=ide.0,bootindex=2',
            '-device', 'qemu-xhci', '-device', 'usb-tablet', '-vga', 'std', '-display', 'none',
            '-vnc', '127.0.0.1:6', '-qmp', 'unix:' + str(QMP) + ',server=on,wait=off',
            '-serial', 'file:' + str(WORK / 'serial.log'),
            '-netdev', 'user,id=net0', '-device', 'e1000e,netdev=net0,id=nic0',
            '-rtc', 'base=localtime']
    if not firmware_only:
        if resume_checks:
            iso = WORK / 'recovery.iso'
            assert iso.is_file()
        else:
            iso = WORK / 'Windows11-Enterprise-Eval-zh-cn.iso'
            source = json.loads((WORK / 'windows-iso-source.json').read_text())
            assert iso.stat().st_size == int(source['content_length'])
        assert (WORK / 'payload.iso').is_file()
        args += ['-drive', 'file=' + str(iso) + ',media=cdrom,if=none,id=installcd,readonly=on',
                 '-device', 'ide-cd,drive=installcd,bus=ide.1' + ('' if resume_checks else ',bootindex=1'),
                 '-drive', 'file=' + str(WORK / 'payload.iso') + ',media=cdrom,if=none,id=payloadcd,readonly=on',
                 '-device', 'ide-cd,drive=payloadcd,bus=ide.2',
                 '-boot', 'menu=on']
    log = (WORK / 'qemu-linux.log').open('ab')
    vm = subprocess.Popen(args, stdout=log, stderr=log)
    (WORK / 'vm-process.json').write_text(json.dumps({'qemu_pid': vm.pid, 'swtpm_pid': tpm.pid,
                                                    'firmware_only': firmware_only, 'args': args}, indent=2))
    try:
        for _ in range(100):
            if QMP.exists():
                break
            assert vm.poll() is None, 'QEMU failed to start'
            time.sleep(.1)
        print(json.dumps({'qmp': qmp('query-status'), 'tpms': qmp('query-tpm'), 'firmware_only': firmware_only}), flush=True)
        if firmware_only:
            time.sleep(hold_seconds)
            qmp('quit')
            assert vm.wait(timeout=30) == 0
            return
        seen = 0
        while vm.poll() is None:
            serial = WORK / 'serial.log'
            raw = serial.read_bytes() if serial.exists() else b''
            lines = raw.splitlines()
            if raw and not raw.endswith(b'\n'):
                lines = lines[:-1]
            for line in lines[seen:]:
                if not line.startswith(b'RMVM:'):
                    continue
                try:
                    event = json.loads(base64.b64decode(line[5:]))
                except (ValueError, UnicodeDecodeError):
                    continue
                with (WORK / 'guest-events.ndjson').open('a', encoding='utf-8') as output:
                    output.write(json.dumps(event, ensure_ascii=False) + '\n')
                print(json.dumps(event, ensure_ascii=False), flush=True)
                phase = event['phase']
                if phase == 'ready-for-offline-check':
                    qmp('set_link', {'name': 'nic0', 'up': False})
                if phase == 'offline-check-complete':
                    qmp('set_link', {'name': 'nic0', 'up': True})
                if phase in ('failed', 'all-tests-passed'):
                    (WORK / 'guest-result.json').write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding='utf-8')
                if phase == 'all-tests-passed':
                    qmp('system_powerdown')
            seen = len(lines)
            time.sleep(1)
        print('VM exited: ' + str(vm.returncode), flush=True)
    finally:
        if vm.poll() is not None:
            try:
                tpm.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tpm.terminate()
                tpm.wait(timeout=10)
            if tpm_socket.exists():
                tpm_socket.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['media', 'firmware-check', 'run', 'status'])
    parser.add_argument('--hold-seconds', type=int, default=5)
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm')
    parser.add_argument('--resume-checks', action='store_true')
    args = parser.parse_args()
    if args.action == 'media':
        media()
    elif args.action == 'status':
        print(json.dumps(qmp('query-status')))
    else:
        assert 1 <= args.hold_seconds <= 300
        launch(args.action == 'firmware-check', args.hold_seconds, args.accel, args.resume_checks)
