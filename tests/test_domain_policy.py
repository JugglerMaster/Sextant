from __future__ import annotations

import stat
from pathlib import Path

from sextant_agent import domain_policy


def _prepare(monkeypatch, tmp_path: Path) -> Path:
    sshd_dir = tmp_path / "sshd_config.d"
    sshd_dir.mkdir()
    sshd = tmp_path / "sshd_config"
    sshd.write_text(
        "# local sshd\nAllowUsers pi christopher dadisc01\n",
        encoding="utf-8",
    )
    pam = tmp_path / "common-session"
    pam.write_text("session required pam_unix.so\n", encoding="utf-8")
    sssd = tmp_path / "sssd.conf"
    sssd.write_text(
        "[sssd]\n"
        "services = nss, pam\n"
        "\n"
        "[domain/corp.example.com]\n"
        "id_provider = ad\n"
        "use_fully_qualified_names = True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(domain_policy, "MANAGED_SSHD", sshd_dir / "90-sextant-domain.conf")
    monkeypatch.setattr(domain_policy, "MANAGED_SUDOERS", tmp_path / "90-sextant-domain")
    monkeypatch.setattr(domain_policy, "SSHD_CONFIG", sshd)
    monkeypatch.setattr(domain_policy, "SSHD_CONFIG_DIR", sshd_dir)
    monkeypatch.setattr(domain_policy, "SSSD_CONFIG", sssd)
    monkeypatch.setattr(domain_policy, "PAM_FILES", (pam,))
    monkeypatch.setattr(
        domain_policy,
        "_realm_joined",
        lambda domain: (True, "configured: kerberos-member"),
    )
    monkeypatch.setattr(domain_policy, "_detect_domain", lambda: "corp.example.com")
    monkeypatch.setattr(domain_policy, "_group_exists", lambda name: True)
    monkeypatch.setattr(domain_policy, "_pam_mkhomedir_available", lambda: True)
    monkeypatch.setattr(
        domain_policy,
        "_run",
        lambda args, timeout=30: {"status": "succeeded", "stdout": "", "stderr": ""},
    )
    return tmp_path


def test_login_policy_writes_managed_configuration(monkeypatch, tmp_path):
    root = _prepare(monkeypatch, tmp_path)
    result = domain_policy.apply_login_policy(
        {
            "ssh_login": True,
            "password_authentication": True,
            "create_home": True,
            "sudo_domain_admins": True,
            "short_names": False,
        }
    )
    assert result["status"] == "succeeded", result
    assert "Domain: corp.example.com" in result["stdout"]

    sshd = (root / "sshd_config").read_text()
    assert domain_policy.SSHD_BLOCK_BEGIN in sshd
    assert "UsePAM yes" in sshd
    assert "PasswordAuthentication yes" in sshd
    assert "KbdInteractiveAuthentication yes" in sshd
    assert not (root / "sshd_config.d" / "90-sextant-domain.conf").exists()

    assert "# Disabled by Sextant domain login policy: AllowUsers pi christopher dadisc01" in sshd
    assert (root / "sshd_config.sextant-backup").exists()

    pam = (root / "common-session").read_text()
    assert "pam_mkhomedir" in pam
    assert domain_policy.PAM_MARKER in pam

    sudoers = (root / "90-sextant-domain").read_text()
    assert "%domain\\ admins@corp.example.com ALL=(ALL:ALL) ALL" in sudoers
    assert stat.S_IMODE((root / "90-sextant-domain").stat().st_mode) == 0o440

    sssd = (root / "sssd.conf").read_text()
    assert "use_fully_qualified_names = True" in sssd


def test_login_policy_short_names_and_disable_removes_managed_files(monkeypatch, tmp_path):
    root = _prepare(monkeypatch, tmp_path)
    result = domain_policy.apply_login_policy(
        {
            "ssh_login": True,
            "password_authentication": False,
            "create_home": False,
            "sudo_domain_admins": True,
            "short_names": True,
        },
        "corp.example.com",
    )
    assert result["status"] == "succeeded", result
    sshd = (root / "sshd_config").read_text()
    assert "PasswordAuthentication no" in sshd
    assert "use_fully_qualified_names = False" in (root / "sssd.conf").read_text()
    assert "pam_mkhomedir" not in (root / "common-session").read_text()

    removed = domain_policy.apply_login_policy(
        {
            "ssh_login": False,
            "password_authentication": True,
            "create_home": False,
            "sudo_domain_admins": False,
            "short_names": False,
        },
        "corp.example.com",
    )
    assert removed["status"] == "succeeded", removed
    sshd = (root / "sshd_config").read_text()
    assert domain_policy.SSHD_BLOCK_BEGIN not in sshd
    assert not (root / "sshd_config.d" / "90-sextant-domain.conf").exists()
    assert not (root / "90-sextant-domain").exists()


def test_login_policy_requires_a_joined_host(monkeypatch):
    monkeypatch.setattr(domain_policy, "_detect_domain", lambda: "")
    result = domain_policy.apply_login_policy({})
    assert result["status"] == "failed"
    assert "not joined" in result["error"]


def test_login_policy_rolls_back_ssh_when_protected_user_would_lose_access(monkeypatch, tmp_path):
    root = _prepare(monkeypatch, tmp_path)
    original = (root / "sshd_config").read_text()
    monkeypatch.setattr(domain_policy, "_local_user_exists", lambda name: True)
    configs = iter(
        [
            {"allowusers": "dadisc01 pi"},
            {"allowusers": "someoneelse"},
        ]
    )
    monkeypatch.setattr(domain_policy, "_sshd_effective_config", lambda: next(configs))

    result = domain_policy.apply_login_policy(
        {"protected_users": ["dadisc01"], "ssh_login": True},
        "corp.example.com",
    )
    assert result["status"] == "failed"
    assert "dadisc01 would lose SSH access" in result["error"]
    assert (root / "sshd_config").read_text() == original
    assert not (root / "sshd_config.d" / "90-sextant-domain.conf").exists()


def test_login_policy_rolls_back_sudo_when_protected_user_would_lose_access(monkeypatch, tmp_path):
    root = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(domain_policy, "_local_user_exists", lambda name: True)
    monkeypatch.setattr(domain_policy, "_pam_mkhomedir_available", lambda: True)
    results = iter([True, False])
    monkeypatch.setattr(domain_policy, "_sudo_allowed", lambda name: next(results))

    result = domain_policy.apply_login_policy(
        {
            "protected_users": ["dadisc01"],
            "ssh_login": True,
            "sudo_domain_admins": True,
        },
        "corp.example.com",
    )
    assert result["status"] == "failed"
    assert "dadisc01 would lose sudo access" in result["error"]
    assert not (root / "90-sextant-domain").exists()


def test_login_policy_uses_dropin_when_sshd_includes_the_directory(monkeypatch, tmp_path):
    root = _prepare(monkeypatch, tmp_path)
    (root / "sshd_config").write_text(
        "Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication no\n",
        encoding="utf-8",
    )
    result = domain_policy.apply_login_policy(
        {"protected_users": [], "ssh_login": True},
        "corp.example.com",
    )
    assert result["status"] == "succeeded", result
    dropin = (root / "sshd_config.d" / "90-sextant-domain.conf").read_text()
    assert "UsePAM yes" in dropin
    assert "PasswordAuthentication yes" in dropin
    assert domain_policy.SSHD_BLOCK_BEGIN not in (root / "sshd_config").read_text()
