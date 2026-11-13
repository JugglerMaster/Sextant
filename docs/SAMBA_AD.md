# Samba AD domain — setup guide

**Status:** Reference setup for a small private network. Not exercised by this
project's CI. Test in a lab before using it for production.

**Scope:** provision a Samba AD DC for Windows domain join, GPO, DNS,
Kerberos, and Sextant LDAP(S) authentication. This is not a Samba file-server
guide, not an AD migration guide, and not a multi-DC replication design.

Related: [Active Directory / Samba AD authentication](AD_LDAP.md),
[Domain management plan](DOMAIN_MANAGEMENT.md),
[Security](SECURITY.md),
[Deployment](DEPLOYMENT.md).

---

## 1. Why Samba AD

Samba AD is the only mature free-software Active Directory-compatible domain
controller. It gives you:

- users, groups, computers, and OUs;
- Kerberos and LDAP(S);
- DNS with AD SRV records;
- Windows 10/11 domain join;
- GPO delivery to Windows clients, edited with RSAT/GPMC.

It is not a drop-in for every AD feature. It does not provide AD CS, a full
RODC role, Exchange schema extensions, or DFS-R SYSVOL replication. Windows
Server AD remains the licensed alternative if you need full parity.

FreeIPA, LLDAP, Kanidm, and Authentik cannot join Windows clients or serve
Windows GPO. They are not alternatives for this requirement.

---

## 2. Assumptions

| Item | Example |
|---|---|
| OS | Debian 13 or Ubuntu 24.04 LTS |
| Samba | 4.20+ (4.19 minimum; use a current release) |
| RAM / disk | 2–4 GB RAM, 20–40 GB disk |
| Hostname | `dc1.corp.example.com` |
| NetBIOS name | `DC1` |
| Realm | `CORP.EXAMPLE.COM` |
| NetBIOS domain | `CORP` |
| Base DN | `DC=corp,DC=example,DC=com` |
| Static IP | `192.168.1.10/24` |
| LAN | `192.168.1.0/24` |
| Upstream DNS | `192.168.1.1` (router) or a Pi-hole/AdGuard address |

Use a **subdomain** you control, such as `corp.example.com` or
`ad.example.com`. Do not use a single-label name and avoid `.local`.

The DC should be a dedicated VM or host. Do not put a Pi-hole, dnsmasq, or
another DNS server on port 53 of the same host.

---

## 3. Prepare the host

Set the hostname and a matching hosts entry:

```bash
sudo hostnamectl set-hostname dc1
sudoedit /etc/hosts
```

```text
127.0.0.1       localhost
192.168.1.10    dc1.corp.example.com dc1
```

Verify:

```bash
hostname -f
# dc1.corp.example.com
```

Configure a static address with your normal network tooling (`/etc/network/interfaces`,
NetworkManager, or netplan). Do not use DHCP for the DC.

Set the clock:

```bash
sudo apt install -y chrony
sudo systemctl enable --now chrony
chronyc tracking
```

Kerberos breaks if the DC clock drifts more than about five minutes. Point
chrony at a reliable upstream or your router.

Samba AD does not provide a full NTP service. If you want clients to use the DC
as their time source, configure chrony with an `allow` line for the LAN and hand
out the DC via DHCP option 42. Otherwise keep your existing NTP source and
verify client clocks independently.

---

## 4. Install Samba

```bash
sudo apt update
sudo apt install -y samba smbclient winbind krb5-user ldb-tools ldap-utils chrony
```

`krb5-user` may prompt for a default realm. You can accept the default; the
provisioning step writes a Samba-specific `krb5.conf` that you copy over it.

Stop the classic Samba services so they do not conflict with the AD DC:

```bash
sudo systemctl disable --now smbd nmbd winbind 2>/dev/null || true
```

On Debian and Ubuntu the AD DC unit is often masked:

```bash
sudo systemctl unmask samba-ad-dc
```

Do not start it until after provisioning.

---

## 5. Provision the domain

Interactive mode is the least error-prone:

```bash
sudo samba-tool domain provision --use-rfc2307 --interactive
```

Answer the prompts:

```text
Realm [SAMDOM.EXAMPLE.COM]: CORP.EXAMPLE.COM
 Domain [SAMDOM]: CORP
 Server Role (dc, member, standalone) [dc]: dc
 DNS backend (SAMBA_INTERNAL, BIND9_FLATFILE, BIND9_DLZ, NONE) [SAMBA_INTERNAL]: SAMBA_INTERNAL
 DNS forwarder IP address (write 'none' to disable forwarding) [192.168.1.1]: 192.168.1.1
Administrator password: <strong password>
Retype password: <strong password>
```

Use `SAMBA_INTERNAL` unless you already operate BIND and need its features.
Internal DNS is the simplest correct choice for a small domain.

`--use-rfc2307` enables the POSIX attributes used by Linux clients and POSIX
identity integration. It cannot be added later without a schema extension, so
enable it now even if you do not use Linux clients yet.

Non-interactive form (avoid putting a password in shell history; prefer the
interactive prompt or a credential file):

```bash
sudo samba-tool domain provision \
  --server-role=dc \
  --use-rfc2307 \
  --dns-backend=SAMBA_INTERNAL \
  --realm=CORP.EXAMPLE.COM \
  --domain=CORP \
  --adminpass='<strong password>'
```

`--function-level=2016` is optional on current Samba releases. Omit it if your
build does not support it; the default is sufficient for Windows 10/11.

The provisioning output ends with the realm, base DN, and domain SID. A typo in
the realm cannot be fixed in place; you would have to reprovision.

Copy the generated Kerberos configuration:

```bash
sudo cp /var/lib/samba/private/krb5.conf /etc/krb5.conf
```

Start the DC:

```bash
sudo systemctl enable --now samba-ad-dc
sudo systemctl status samba-ad-dc --no-pager
```

Do not run `smbd`, `nmbd`, or `winbind` alongside `samba-ad-dc` on the DC.

---

## 6. Verify the basics

```bash
sudo samba-tool domain level show
sudo samba-tool domain info 127.0.0.1
sudo smbclient -L localhost -U%
```

You should see `SYSVOL` and `NETLOGON` shares and the domain information.

Kerberos:

```bash
kinit administrator@CORP.EXAMPLE.COM
klist
```

DNS:

```bash
host -t SRV _ldap._tcp.corp.example.com 127.0.0.1
host -t SRV _kerberos._tcp.corp.example.com 127.0.0.1
sudo samba-tool dns query 127.0.0.1 corp.example.com @ ALL
```

If the SRV lookups fail, stop and fix DNS before joining any Windows client.

---

## 7. DNS and DHCP

### Forwarder

Check `/etc/samba/smb.conf`:

```ini
[global]
    dns forwarder = 192.168.1.1
```

If provisioning copied `127.0.0.53` from `systemd-resolved`, replace it with the
real upstream or you will create a loop. Restart `samba-ad-dc` after editing.

### Resolver on the DC

Samba DNS must own port 53. On a host with `systemd-resolved`, disable the stub listener or disable the
service (if present):

```bash
sudo systemctl disable --now systemd-resolved 2>/dev/null || true
sudo rm -f /etc/resolv.conf
```

```bash
sudo tee /etc/resolv.conf >/dev/null <<'EOF'
nameserver 127.0.0.1
search corp.example.com
EOF
```

If you keep `systemd-resolved`, set `DNSStubListener=no` in
`/etc/systemd/resolved.conf`, restart it, and point `/etc/resolv.conf` at
`127.0.0.1`.

### DHCP

Set DHCP option 6 (DNS server) to the DC address for domain clients, and set
the domain name to `corp.example.com`.

If you use Pi-hole or AdGuard for the rest of the network, you have two clean
choices:

1. Hand the DC to domain clients as DNS; the DC forwards everything else to
   Pi-hole.
2. Keep Pi-hole as client DNS and add conditional forwards for
   `corp.example.com` and `_msdcs.corp.example.com` to the DC.

Do not hand out a non-DC DNS server first to domain clients. Domain join needs
the AD SRV records.

A reverse zone is optional. Add one if you want PTR records for the DC and
clients.

---

## 8. LDAPS

Samba enables TLS by default and generates a self-signed certificate and CA on
first startup:

```text
/var/lib/samba/private/tls/cert.pem
/var/lib/samba/private/tls/key.pem
/var/lib/samba/private/tls/ca.pem
```

On a source build the prefix is `/usr/local/samba/private/tls/`.

Check the certificate:

```bash
sudo openssl x509 -in /var/lib/samba/private/tls/cert.pem \
  -noout -subject -issuer -dates -ext subjectAltName
```

Test LDAPS:

```bash
openssl s_client -connect dc1.corp.example.com:636 \
  -CAfile /var/lib/samba/private/tls/ca.pem \
  -servername dc1.corp.example.com </dev/null
```

If the certificate has no SAN for `dc1.corp.example.com`, replace it. Modern
clients expect a SAN even when a particular library does not check the hostname.

### Custom certificate

Put the key and certificate somewhere root-readable, mode 0600 for the key:

```text
/etc/samba/tls/dc1.key
/etc/samba/tls/dc1.crt
/etc/samba/tls/ca.crt
```

Add to `[global]` in `/etc/samba/smb.conf`:

```ini
    tls enabled = yes
    tls keyfile = /etc/samba/tls/dc1.key
    tls certfile = /etc/samba/tls/dc1.crt
    tls cafile = /etc/samba/tls/ca.crt
```

For a self-signed certificate, `tls cafile` may be empty. Restart:

```bash
sudo systemctl restart samba-ad-dc
```

Use a private CA (step-ca or your existing PKI) rather than a public CA. The
certificate needs `DNS:dc1.corp.example.com` and the server-auth EKU.

### Test the LDAP bind Sextant will use

```bash
ldapsearch -H ldaps://dc1.corp.example.com:636 -x \
  -D "CN=svc-sextant,OU=Service Accounts,DC=corp,DC=example,DC=com" -W \
  -b "DC=corp,DC=example,DC=com" \
  "(sAMAccountName=alice)" displayName mail memberOf
```

Expected: exactly one entry, with `memberOf` containing the Sextant role group
DN. If this works with `ldapsearch` but not Sextant, check the CA bundle, the
bind DN, and the filter.

Copy the CA to the Sextant host:

```bash
sudo install -d -m 0755 /etc/sextant
sudo install -m 0644 /var/lib/samba/private/tls/ca.pem /etc/sextant/samba-ca.pem
```

The `sextant` service user must be able to read the bundle. The CA is public, so
mode 0644 is fine; the bind password must stay in the mode-0600 environment
file.

---

## 9. Users, groups, and OUs

Create an OU structure. Full DNs are clearer than relative names:

```bash
sudo samba-tool ou add "OU=People,DC=corp,DC=example,DC=com"
sudo samba-tool ou add "OU=Groups,DC=corp,DC=example,DC=com"
sudo samba-tool ou add "OU=Workstations,DC=corp,DC=example,DC=com"
sudo samba-tool ou add "OU=Servers,DC=corp,DC=example,DC=com"
sudo samba-tool ou add "OU=Service Accounts,DC=corp,DC=example,DC=com"
```

Create the Sextant role groups:

```bash
sudo samba-tool group add "RC Admins" --groupou="OU=Groups"
sudo samba-tool group add "RC Operators" --groupou="OU=Groups"
sudo samba-tool group add "RC Viewers" --groupou="OU=Groups"
```

Create users. Omit the password to be prompted, or pass a placeholder from your
password manager; do not paste a real password into shell history:

```bash
sudo samba-tool user add alice \
  --given-name=Alice --surname=Example \
  --mail-address=alice@corp.example.com \
  --userou="OU=People"
```

Add members:

```bash
sudo samba-tool group addmembers "RC Admins" alice
sudo samba-tool group addmembers "RC Operators" bob
sudo samba-tool group addmembers "RC Viewers" carol
```

Create a read-only bind account for Sextant. A normal user is enough; do not
add it to Domain Admins:

```bash
sudo samba-tool user add svc-sextant \
  --description="Sextant LDAP bind and directory inventory" \
  --userou="OU=Service Accounts"
sudo samba-tool user setexpiry svc-sextant --noexpiry
```

`--userou` and `--groupou` take the container DN **relative to the domain**
(`OU=People`, not `OU=People,DC=corp,DC=example,DC=com`). Samba appends the
domain DN. If you pass the full DN, the object DN is built twice and creation
fails.

Verify the DNs:

```bash
sudo samba-tool group listmembers "RC Admins" --full-dn
sudo samba-tool user getgroups alice
sudo samba-tool user show svc-sextant
```

AD user DNs use `CN`, not `sAMAccountName`. A user created with
`--given-name=Alice --surname=Example` is normally
`CN=Alice Example,OU=People,...`; a service account without a display name is
normally `CN=svc-sextant,OU=Service Accounts,...`. Use the DN printed by
`samba-tool user show` in `SEXTANT_LDAP_BIND_DN`.

Useful lifecycle commands:

```bash
sudo samba-tool user disable alice
sudo samba-tool user enable alice
sudo samba-tool user setpassword alice
sudo samba-tool user unlock alice
```

Set a basic password policy:

```bash
sudo samba-tool domain passwordsettings set \
  --complexity=on \
  --min-pwd-length=12 \
  --history-length=24
sudo samba-tool domain passwordsettings show
```

For Sextant, keep the role groups flat. Sextant reads direct `memberOf`
membership for login; nested groups are not expanded in the authentication
path. The planned directory inventory in
[DOMAIN_MANAGEMENT.md](DOMAIN_MANAGEMENT.md) can expand nested groups for
targeting, but login role mapping remains direct.

---

## 10. Join Windows clients

### Client prerequisites

- Windows 10/11 Pro, Enterprise, or Education. Home cannot join a domain.
- The client's DNS server is the DC (or a resolver with working conditional
  forwards).
- The client clock is within five minutes of the DC.
- TCP/UDP required ports are open between client and DC.

### Join

Elevated PowerShell:

```powershell
Add-Computer `
  -DomainName corp.example.com `
  -OUPath "OU=Workstations,DC=corp,DC=example,DC=com" `
  -Credential (Get-Credential CORP\Administrator) `
  -Restart
```

Or use `sysdm.cpl` → Change → Domain and move the computer object to the target
OU afterward with ADUC/RSAT.

Verify after reboot:

```powershell
nltest /dsgetdc:corp.example.com
whoami
gpupdate /force
gpresult /r
```

The default `ms-DS-MachineAccountQuota` lets each authenticated user join up to
10 computers. For more, pre-create computer accounts or delegate "Create
Computer Objects" on the target OU. Do not raise the quota blindly.

If the join fails, check in this order: DNS resolution of the AD SRV records,
client DNS server, clock skew, firewall, and Samba version.

---

## 11. Group Policy

### RSAT

Install RSAT on a Windows Pro/Enterprise admin machine:

```powershell
Add-WindowsCapability -Online -Name "Rsat.GroupPolicy.Management.Tools~~~~0.0.1.0"
Add-WindowsCapability -Online -Name "Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0"
Add-WindowsCapability -Online -Name "Rsat.Dns.Tools~~~~0.0.1.0"
```

Open `gpmc.msc`, connect to `corp.example.com`, create a GPO, link it to an OU,
and edit it.

### Central store

For consistent ADMX templates, populate:

```text
\\corp.example.com\SYSVOL\corp.example.com\Policies\PolicyDefinitions
```

Copy the ADMX/ADML files from a current Windows administrative-templates
download. Samba can also load templates:

```bash
sudo samba-tool gpo admxload -U Administrator \
  --admx-dir=/path/to/PolicyDefinitions
```

### CLI

```bash
sudo samba-tool gpo listall
sudo samba-tool gpo create "Sextant Baseline - Workstations"
sudo samba-tool gpo listcontainers "{GUID}"
sudo samba-tool gpo setlink \
  "OU=Workstations,DC=corp,DC=example,DC=com" "{GUID}"
```

Use GPMC for editing. `samba-tool gpo` can create, link, and list, but it is not
a full policy editor.

### Test a policy

Apply a harmless registry value to a test OU, then on a client:

```powershell
gpupdate /force
gpresult /h C:\gp.html
```

Open `gp.html` and confirm the GPO is listed as applied. Do not start with a
security baseline or a logon restriction; a bad GPO can lock you out.

### Caveats

- GPO delivery to Windows clients works; RSAT/GPMC is required for editing.
- Some Windows-specific extensions and CSEs may not be fully supported. Test the
  exact policies you need.
- Samba does not provide AD CS, RODC, or Exchange schema extensions.
- Samba does not implement DFS-R. Multi-DC SYSVOL replication needs a
  documented workaround (for example rsync/unison) and careful testing. For a
  small deployment, one DC plus tested backups is simpler and safer.
- Do not edit `Registry.pol` or SYSVOL by hand. Use GPMC/PowerShell.
- If SYSVOL ACLs break, `samba-tool ntacl sysvolcheck` and
  `samba-tool ntacl sysvolreset` are the repair tools.

---

## 12. Windows Update

Samba delivers the GPO; it does not patch Windows.

### WSUS

Run WSUS on a Windows Server member (a separate VM is fine), join it to the
domain, and configure the client GPO:

```text
Computer Configuration
  > Administrative Templates
  > Windows Components
  > Windows Update
    > Specify intranet Microsoft update service location
```

Point clients at the WSUS URL, put them in a target group, and approve updates
in the WSUS console. WSUS is the native self-hosted per-update approval engine.
It requires a Windows Server license.

### Windows Update for Business

If you do not want a Windows Server VM, use WUfB deferral policies instead:

```text
Computer Configuration
  > Administrative Templates
  > Windows Components
  > Windows Update
  > Manage updates offered from Windows Update
```

WUfB gives deferral rings, pause, and active hours, but no per-update approval.
Sextant can report installed/pending state and trigger a scan; it should not
install OS updates.

Do not use `wuauclt` for automation. It is deprecated and version-dependent.

---

## 13. Sextant configuration

On the Sextant server, install the optional LDAP dependency in the venv used by
the running release:

```bash
sudo /opt/sextant/.venv/bin/pip install 'ldap3>=2.9,<3'
```

If you use the versioned release layout, `/opt/sextant/current` points at the
active venv; install into `/opt/sextant/current/bin/pip` instead. The base
lockfile omits `ldap3`, so verify the dependency after a server update.

Ensure the Sextant host can resolve `dc1.corp.example.com`. Use the DC as DNS,
a conditional forward, or a static `/etc/hosts` entry. LDAPS hostname
verification and certificate SAN matching depend on it.

Set the directory variables in `/etc/sextant.env`:

```dotenv
SEXTANT_LDAP_ENABLED=1
SEXTANT_LDAP_SERVER=dc1.corp.example.com
SEXTANT_LDAP_PORT=636
SEXTANT_LDAP_USE_SSL=1
SEXTANT_LDAP_CA_CERT=/etc/sextant/samba-ca.pem

SEXTANT_LDAP_BASE_DN=DC=corp,DC=example,DC=com
SEXTANT_LDAP_BIND_DN=CN=svc-sextant,OU=Service Accounts,DC=corp,DC=example,DC=com
SEXTANT_LDAP_BIND_PASSWORD=use-a-password-manager

SEXTANT_LDAP_USER_FILTER=(sAMAccountName={username})
SEXTANT_LDAP_ADMIN_GROUPS=CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com
SEXTANT_LDAP_OPERATOR_GROUPS=CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com
SEXTANT_LDAP_VIEWER_GROUPS=CN=RC Viewers,OU=Groups,DC=corp,DC=example,DC=com
SEXTANT_LDAP_DEFAULT_ROLE=viewer
```

Then:

```bash
sudo systemctl restart sextant
```

Log in with `alice` and the domain password. The login page shows the
**Active Directory / Samba** mode selector when LDAP is enabled.

Keep a local admin account as break-glass. Sextant deliberately retains local
accounts so a directory outage does not lock you out.

If Sextant returns `503 Directory authentication is unavailable`, check LDAPS
reachability, the CA bundle, and the service bind DN/password. If it returns
`401`, check the user filter, the user's password, and duplicate search matches.

---

## 14. Backup and recovery

Take a domain-aware backup while the DC is running:

```bash
sudo install -d -m 0700 /srv/backup
sudo samba-tool domain backup online \
  --targetdir=/srv/backup \
  --server=dc1.corp.example.com \
  -U Administrator
```

Copy the resulting tar file off-host, encrypt it, and keep a retention policy.
It contains the directory database and SYSVOL.

Offline backup (stop `samba-ad-dc` first):

```bash
sudo systemctl stop samba-ad-dc
sudo samba-tool domain backup offline --targetdir=/srv/backup
sudo systemctl start samba-ad-dc
```

Restore into a new target, not over a live DC:

```bash
sudo samba-tool domain backup restore \
  --backup-file=/srv/backup/samba-backup-<timestamp>.tar.bz2 \
  --newservername=restore1 \
  --targetdir=/srv/restore/samba
```

Use a new, empty target directory and a new server name when restoring
alongside the live DC. For in-place disaster recovery, use the original server
name and a fresh host. Do not restore over `/var/lib/samba` on a running
system. Test the restore in a lab. A backup you have never restored is a hope,
not a backup.

Do not use a hypervisor snapshot as your only DC backup. AD is stateful, and
restoring an old snapshot can cause replication or USN rollback problems.

A second DC is possible:

```bash
sudo samba-tool domain join corp.example.com DC \
  --dns-backend=SAMBA_INTERNAL \
  --option='idmap_ldb:use rfc2307 = yes' \
  -U Administrator
sudo systemctl unmask samba-ad-dc
sudo systemctl enable --now samba-ad-dc
sudo samba-tool drs showrepl
```

But multi-DC adds SYSVOL replication, DNS, FSMO, and time dependencies. Samba
does not implement DFS-R, so SYSVOL/GPO replication needs a documented
workaround and verification. For a small network, one well-backed-up DC is
usually the better trade.

---

## 15. Firewall

Allow the LAN, not the internet. Required ports:

| Port | Protocol | Service |
|---|---|---|
| 53 | TCP/UDP | DNS |
| 88 | TCP/UDP | Kerberos |
| 123 | UDP | NTP (if this host serves time) |
| 135 | TCP | RPC endpoint mapper |
| 137 | UDP | NetBIOS name |
| 138 | UDP | NetBIOS datagram |
| 139 | TCP | NetBIOS session (legacy) |
| 389 | TCP/UDP | LDAP |
| 445 | TCP | SMB (SYSVOL/NETLOGON) |
| 464 | TCP/UDP | Kerberos password change |
| 636 | TCP | LDAPS |
| 3268 | TCP | Global Catalog |
| 3269 | TCP | Global Catalog over TLS |
| 49152–65535 | TCP | Dynamic RPC |

Example with `ufw`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 53 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 53 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 88 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 88 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 123 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 137 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 138 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 139 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 389 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 636 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 445 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 135 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 464 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 464 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 3268 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 3269 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 49152:65535 proto tcp
```

If the Sextant host is on a different VLAN, add its subnet to the 636/tcp rule
(and to 53, 88, and 445 if it performs directory lookups or GPO work). Do not
expose LDAP, SMB, RPC, or the DC to the public internet.

---

## 16. Operations

### Health checks

```bash
sudo samba-tool domain level show
sudo samba-tool dbcheck --cross-ncs
sudo samba-tool ntacl sysvolcheck
sudo samba-tool drs showrepl        # multi-DC only
sudo samba-tool domain passwordsettings show
```

### Logs

```bash
sudo journalctl -u samba-ad-dc -n 100 --no-pager
sudo ls -l /var/log/samba/
```

### Upgrades

- Read the Samba release notes before upgrading.
- Take a `samba-tool domain backup online` first.
- Upgrade the DC in a maintenance window.
- Re-run `samba-tool dbcheck --cross-ncs` and `samba-tool ntacl sysvolcheck`.
- Join a test Windows client and run `gpupdate /force` after the upgrade.

### Monitoring

Watch DNS SRV records, LDAPS on 636, Kerberos on 88, clock skew, disk space,
and backup age. A DC that cannot resolve its own SRV records will fail domain
joins even though `systemctl status` looks healthy.

---

## 17. Limitations

- Samba AD is not a complete replacement for every Windows AD feature.
- No built-in AD CS/Enterprise CA. Use step-ca or OpenSSL for the LDAPS
  certificate.
- No full RODC role.
- No Exchange schema extension.
- GPO editing requires RSAT/GPMC; some CSEs may not be fully supported.
- No DFS-R SYSVOL replication. Multi-DC SYSVOL sync needs a workaround.
- Functional level depends on the Samba release; current releases support
  modern levels, older ones default lower.
- The AD DC is not a general file server. Use a separate file server or NAS.
- Windows 10/11 join works with current Samba releases, but test your exact
  Windows build and Samba package.
- There is no Microsoft support contract for Samba AD.

---

## 18. Troubleshooting

| Symptom | Check |
|---|---|
| Windows cannot join the domain | Client DNS points at the DC; SRV records resolve; clock skew < 5 min; firewall; Samba version |
| `kinit` fails | `/etc/krb5.conf` copied; DNS; clock; realm case |
| LDAPS connection fails | Port 636; cert SAN; CA bundle; `tls enabled`; restart |
| Sextant returns 503 | LDAPS reachability; `SEXTANT_LDAP_CA_CERT`; bind DN/password |
| Sextant returns 401 | User filter; password; duplicate entries; wrong `sAMAccountName` |
| GPO not applied | GPO linked to the OU; security filtering; WMI filter; SYSVOL reachable; `gpupdate /force`; `gpresult /h` |
| DNS forwarder loop | `dns forwarder` is `127.0.0.53`; fix to the real upstream |
| Port 53 already in use | `systemd-resolved`, dnsmasq, or Pi-hole on the DC host |
| `samba-ad-dc` will not start | Unit masked; `smbd`/`nmbd`/`winbind` conflict; `testparm`; journal |
| SYSVOL ACL errors | `samba-tool ntacl sysvolcheck`; `samba-tool ntacl sysvolreset` |
| `samba-tool` asks for credentials | `kinit administrator@REALM` or pass `-U Administrator` |

---

## 19. Minimal checklist

```text
[ ] Static IP, FQDN hostname, /etc/hosts
[ ] chrony, correct time
[ ] Samba 4.20+, smbd/nmbd/winbind disabled
[ ] samba-tool domain provision --use-rfc2307
[ ] /etc/krb5.conf copied
[ ] samba-ad-dc enabled and healthy
[ ] DNS forwarder fixed; resolv.conf points at the DC
[ ] SRV records resolve
[ ] LDAPS certificate has the right SAN; port 636 works
[ ] OUs, role groups, users, service account
[ ] Windows client joins and `gpresult` works
[ ] GPO created/linked and tested on a canary client
[ ] Windows Update policy chosen (WSUS or WUfB)
[ ] Sextant LDAP settings and CA bundle
[ ] Online domain backup copied off-host and restore tested
[ ] Firewall limited to the LAN
```
