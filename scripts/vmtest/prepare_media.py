"""Prepare answer file/test media metadata, without changing the host OS."""
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.build/clean-vm'
MEDIA = WORK / 'media'
RELEASE = ROOT / 'output/ReadMaster-0.5.0.1'
WCM = 'http://schemas.microsoft.com/WMIConfig/2002/State'
ET.register_namespace('wcm', WCM)


def add(parent, tag, value=None, **attributes):
    element = ET.SubElement(parent, tag, attributes)
    if value is not None:
        element.text = str(value)
    return element


def component(settings, name):
    return add(settings, 'component', name=name, processorArchitecture='amd64',
               publicKeyToken='31bf3856ad364e35', language='neutral', versionScope='nonSxS')


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    (MEDIA / 'vmtest').mkdir(exist_ok=True)
    for name in ('start_guest.ps1', 'guest_checks.py'):
        shutil.copy2(Path(__file__).parent / name, MEDIA / 'vmtest' / name)
    (MEDIA / 'READMASTER_VM_MEDIA').write_text('Isolated ReadMaster installer validation; synthetic data only.\n')
    manifest = json.loads((RELEASE / 'release-manifest.json').read_text())
    for asset in manifest['assets']:
        path = RELEASE / asset['name']
        assert path.stat().st_size == asset['bytes']
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == asset['sha256']
    password_path = WORK / 'local-lab-password.txt'
    if not password_path.exists():
        password_path.write_text(secrets.token_urlsafe(24) + 'Aa1!')
    password = password_path.read_text()
    root = ET.Element('unattend', xmlns='urn:schemas-microsoft-com:unattend')
    pe = add(root, 'settings', **{'pass': 'windowsPE'})
    intl = component(pe, 'Microsoft-Windows-International-Core-WinPE')
    add(add(intl, 'SetupUILanguage'), 'UILanguage', 'zh-CN')
    for key in ('InputLocale', 'SystemLocale', 'UILanguage', 'UserLocale'):
        add(intl, key, '0804:00000804' if key == 'InputLocale' else 'zh-CN')
    setup = component(pe, 'Microsoft-Windows-Setup')
    add(add(setup, 'DynamicUpdate'), 'Enable', 'false')
    disk = add(add(setup, 'DiskConfiguration'), 'Disk', **{f'{{{WCM}}}action': 'add'})
    add(disk, 'DiskID', 0)
    add(disk, 'WillWipeDisk', 'true')
    creates = add(disk, 'CreatePartitions')
    for order, kind, size in [(1, 'EFI', 260), (2, 'MSR', 16), (3, 'Primary', None)]:
        part = add(creates, 'CreatePartition', **{f'{{{WCM}}}action': 'add'})
        add(part, 'Order', order)
        add(part, 'Type', kind)
        add(part, 'Size' if size else 'Extend', size if size else 'true')
    modifies = add(disk, 'ModifyPartitions')
    for order, number, fmt, label, letter in [(1, 1, 'FAT32', 'System', 'S'), (2, 3, 'NTFS', 'Windows', 'C')]:
        part = add(modifies, 'ModifyPartition', **{f'{{{WCM}}}action': 'add'})
        for key, value in [('Order', order), ('PartitionID', number), ('Format', fmt), ('Label', label), ('Letter', letter)]:
            add(part, key, value)
    image = add(add(setup, 'ImageInstall'), 'OSImage')
    metadata = add(add(image, 'InstallFrom'), 'MetaData', **{f'{{{WCM}}}action': 'add'})
    add(metadata, 'Key', '/IMAGE/INDEX')
    add(metadata, 'Value', 1)
    target = add(image, 'InstallTo')
    add(target, 'DiskID', 0)
    add(target, 'PartitionID', 3)
    add(image, 'WillShowUI', 'OnError')
    user = add(setup, 'UserData')
    add(user, 'AcceptEula', 'true')
    add(user, 'FullName', 'ReadMaster VM Test')
    add(user, 'Organization', 'Local installation test')
    specialized = component(add(root, 'settings', **{'pass': 'specialize'}), 'Microsoft-Windows-Shell-Setup')
    add(specialized, 'ComputerName', 'RM-CLEAN-VM')
    add(specialized, 'TimeZone', 'China Standard Time')
    oobe = add(root, 'settings', **{'pass': 'oobeSystem'})
    shell = component(oobe, 'Microsoft-Windows-Shell-Setup')
    welcome = add(shell, 'OOBE')
    for key in ('HideEULAPage', 'HideOnlineAccountScreens', 'HideWirelessSetupInOOBE', 'HideOEMRegistrationScreen'):
        add(welcome, key, 'true')
    add(welcome, 'ProtectYourPC', 1)
    account = add(add(add(shell, 'UserAccounts'), 'LocalAccounts'), 'LocalAccount', **{f'{{{WCM}}}action': 'add'})
    for key, value in [('Name', 'VMTest'), ('DisplayName', 'ReadMaster VM Test'), ('Group', 'Administrators')]:
        add(account, key, value)
    pwd = add(account, 'Password')
    add(pwd, 'Value', password)
    add(pwd, 'PlainText', 'true')
    auto = add(shell, 'AutoLogon')
    for key, value in [('Enabled', 'true'), ('Username', 'VMTest'), ('LogonCount', 3)]:
        add(auto, key, value)
    pwd = add(auto, 'Password')
    add(pwd, 'Value', password)
    add(pwd, 'PlainText', 'true')
    first = add(add(shell, 'FirstLogonCommands'), 'SynchronousCommand', **{f'{{{WCM}}}action': 'add'})
    add(first, 'Order', 1)
    add(first, 'Description', 'Validate ReadMaster in the isolated clean guest')
    command = ('powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "'
               "$m = Get-PSDrive -PSProvider FileSystem | Where-Object { Test-Path (Join-Path $_.Root 'READMASTER_VM_MEDIA') }; "
               "& (Join-Path $m.Root 'vmtest\\start_guest.ps1')\"")
    add(first, 'CommandLine', command)
    ET.indent(root)
    ET.ElementTree(root).write(MEDIA / 'Autounattend.xml', encoding='utf-8', xml_declaration=True)
    # The lab credential/answer file stays in the ignored VM work directory, never in release assets.
    report = {'release_manifest': manifest, 'memory_mib': 12288, 'vcpus': 6,
              'disk_gib': 96, 'uefi_secure_boot': True, 'virtual_tpm': '2.0',
              'disk_guard': 'Only a new qcow2 image is attached; no host disk passthrough.',
              'guest_network': 'NAT; physically disconnected for installed model/PDF checks.',
              'ui_scope': 'Unmodified bootstrapper using sibling release assets; unmodified full installer.',
              'credentials': 'Synthetic local lab account only; no user account or document copied.'}
    (WORK / 'test-plan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('memory_mib', 'vcpus', 'disk_gib', 'uefi_secure_boot', 'virtual_tpm')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
