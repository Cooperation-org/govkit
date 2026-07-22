"""
Magic-link invites: Invite model lifecycle, the doorway S2S API, the accept ceremony,
and the mint UI (direct vs doorway links). Contract: scratch.md "MAGIC-LINK CONTRACT"
+ "DASHBOARD SESSION REPLY".
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.orgs.models import Invite, InviteStatus, Membership, MembershipRole

S2S_TOKEN = "test-s2s-secret"


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="accel", display_name="Earned Gov Accelerator")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


@pytest.fixture
def invite(admin_org):
    org, admin = admin_org
    return Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience="mentor",
        name="Jane Doe",
        email="jane@example.com",
        link="https://linkedin.com/in/janedoe",
        image_url="https://example.com/jane.png",
        drafted_statement="I commit to mentoring one founder this cohort.",
        drafted_social_post="Joining the accelerator as a mentor.",
        created_by=admin,
    )


def _accept_url(invite):
    return reverse("orgs:accept_invite", kwargs={"code": invite.code})


def _detail_url(invite):
    return reverse("s2s_invite_detail", kwargs={"org_slug": invite.org.slug, "code": invite.code})


def _committed_url(invite):
    return reverse(
        "s2s_invite_committed", kwargs={"org_slug": invite.org.slug, "code": invite.code}
    )


def _auth():
    return {"HTTP_AUTHORIZATION": f"Bearer {S2S_TOKEN}"}


# --- Model lifecycle ---------------------------------------------------------------------


@pytest.mark.django_db
def test_invite_defaults(invite):
    assert invite.status == InviteStatus.CREATED
    assert len(invite.code) >= 16
    assert invite.can_accept
    assert not invite.is_expired
    # ~30-day default expiry.
    assert abs((invite.expires_at - timezone.now()).days - 30) <= 1


@pytest.mark.django_db
def test_invite_codes_are_unique(admin_org):
    org, _ = admin_org
    codes = {Invite.objects.create(org=org).code for _ in range(20)}
    assert len(codes) == 20


@pytest.mark.django_db
def test_mark_committed_and_idempotency(invite):
    invite.mark_committed(claim_id=101, statement_as_published="My words.", video_url="")
    invite.refresh_from_db()
    assert invite.status == InviteStatus.COMMITTED
    assert invite.committed_claim_id == 101
    assert invite.statement_as_published == "My words."
    assert invite.can_accept  # commit does not consume the invite

    # A second commit is a no-op: the first claim wins.
    invite.mark_committed(claim_id=999, statement_as_published="Other words.")
    invite.refresh_from_db()
    assert invite.committed_claim_id == 101


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,expected",
    [
        (InviteStatus.CREATED, True),
        (InviteStatus.COMMITTED, True),
        (InviteStatus.ACCEPTED, False),
        (InviteStatus.REVOKED, False),
    ],
)
def test_can_accept_by_status(invite, status, expected):
    invite.status = status
    invite.save(update_fields=["status"])
    assert invite.can_accept is expected


@pytest.mark.django_db
def test_expired_invite_cannot_accept(invite):
    invite.expires_at = timezone.now() - timedelta(minutes=1)
    invite.save(update_fields=["expires_at"])
    assert invite.is_expired
    assert not invite.can_accept
    assert "expired" in invite.status_label


# --- Doorway S2S API ----------------------------------------------------------------------


@pytest.mark.django_db
def test_s2s_requires_bearer_token(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    assert client.get(_detail_url(invite)).status_code == 401
    assert client.get(_detail_url(invite), HTTP_AUTHORIZATION="Bearer wrong").status_code == 401


@pytest.mark.django_db
def test_s2s_disabled_when_token_unset(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = ""
    assert client.get(_detail_url(invite), **_auth()).status_code == 401


@pytest.mark.django_db
def test_s2s_detail_happy_path(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    resp = client.get(_detail_url(invite), **_auth())
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["name"] == "Jane Doe"
    assert body["email"] == "jane@example.com"
    assert body["link"] == "https://linkedin.com/in/janedoe"
    assert body["image_url"] == "https://example.com/jane.png"
    assert body["role"] == MembershipRole.MEMBER
    assert body["audience"] == "mentor"
    assert body["drafted_statement"] == "I commit to mentoring one founder this cohort."
    assert body["drafted_social_post"] == "Joining the accelerator as a mentor."
    assert body["status"] == InviteStatus.CREATED
    assert body["org_slug"] == "accel"
    assert body["org_name"] == "Earned Gov Accelerator"
    assert body["expires_at"]
    assert body["accept_url"].endswith(_accept_url(invite))


@pytest.mark.django_db
def test_s2s_detail_unknown_code_or_wrong_org_404(client, invite, org_factory, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    other = org_factory(slug="other")
    unknown = reverse("s2s_invite_detail", kwargs={"org_slug": invite.org.slug, "code": "nope"})
    wrong_org = reverse("s2s_invite_detail", kwargs={"org_slug": other.slug, "code": invite.code})
    assert client.get(unknown, **_auth()).status_code == 404
    assert client.get(wrong_org, **_auth()).status_code == 404


@pytest.mark.django_db
def test_s2s_committed_happy_path_and_idempotency(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    resp = client.post(
        _committed_url(invite),
        {"claim_id": 42, "statement_as_published": "As published.", "video_url": ""},
        content_type="application/json",
        **_auth(),
    )
    assert resp.status_code == 200, resp.content
    invite.refresh_from_db()
    assert invite.status == InviteStatus.COMMITTED
    assert invite.committed_claim_id == 42

    # Idempotent replay: 200, current state, first claim id kept.
    resp = client.post(
        _committed_url(invite),
        {"claim_id": 43},
        content_type="application/json",
        **_auth(),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == InviteStatus.COMMITTED
    invite.refresh_from_db()
    assert invite.committed_claim_id == 42


@pytest.mark.django_db
def test_s2s_committed_conflict_when_dead(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    invite.status = InviteStatus.REVOKED
    invite.save(update_fields=["status"])
    resp = client.post(
        _committed_url(invite), {"claim_id": 1}, content_type="application/json", **_auth()
    )
    assert resp.status_code == 409

    invite.status = InviteStatus.CREATED
    invite.expires_at = timezone.now() - timedelta(minutes=1)
    invite.save(update_fields=["status", "expires_at"])
    resp = client.post(
        _committed_url(invite), {"claim_id": 1}, content_type="application/json", **_auth()
    )
    assert resp.status_code == 409


@pytest.mark.django_db
def test_s2s_committed_requires_claim_id(client, invite, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    resp = client.post(
        _committed_url(invite),
        {"statement_as_published": "no claim"},
        content_type="application/json",
        **_auth(),
    )
    assert resp.status_code == 400
    invite.refresh_from_db()
    assert invite.status == InviteStatus.CREATED


# --- Accept ceremony ------------------------------------------------------------------------


@pytest.mark.django_db
def test_accept_from_committed_state(client, invite, user_factory):
    """A doorway invite arrives at accept already committed — that path must join too."""
    invite.mark_committed(claim_id=7)
    user = user_factory(email="jane@example.com")
    client.force_login(user)
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": invite.org.slug})
    assert Membership.objects.filter(org=invite.org, user=user).exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED


@pytest.mark.django_db
def test_accept_dead_invite_bounces_to_landing(client, invite, user_factory):
    invite.status = InviteStatus.REVOKED
    invite.save(update_fields=["status"])
    user = user_factory()
    client.force_login(user)
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(org=invite.org, user=user).exists()


@pytest.mark.django_db
def test_accepted_invite_is_single_use(client, invite, user_factory):
    first = user_factory()
    client.force_login(first)
    client.get(_accept_url(invite))

    second_client_user = user_factory()
    client.logout()
    client.force_login(second_client_user)
    resp = client.get(_accept_url(invite))
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(org=invite.org, user=second_client_user).exists()


@pytest.mark.django_db
def test_existing_member_preview_keeps_role_and_invite(
    client, invite, user_factory, membership_factory
):
    """An existing member on an invite link (the inviter previewing) is never
    re-roled AND never burns the single-use code — it stays live for the invitee."""
    user = user_factory()
    membership_factory(org=invite.org, user=user, role=MembershipRole.ADMIN)
    invite.role = MembershipRole.MEMBER
    invite.save(update_fields=["role"])
    client.force_login(user)
    client.get(_accept_url(invite))
    assert Membership.objects.get(org=invite.org, user=user).role == MembershipRole.ADMIN
    invite.refresh_from_db()
    assert invite.status == InviteStatus.CREATED  # still live for the real invitee


@pytest.mark.django_db
def test_stale_session_code_is_harmless_on_login(client, invite, user_factory, settings):
    """If the stashed invite dies before login completes, login still succeeds."""
    settings.GOVKIT_DEV_LOGIN = True
    client.get(_accept_url(invite))  # stash code
    invite.status = InviteStatus.REVOKED
    invite.save(update_fields=["status"])
    user_factory(email="late@example.com")
    resp = client.post(
        reverse("accounts:dev_login"), {"email": "late@example.com", "password": "pw12345!"}
    )
    assert resp.status_code == 302  # lands somewhere sane, no membership, no crash
    assert not Membership.objects.filter(org=invite.org).exclude(role="admin").exists()


# --- Mint UI ---------------------------------------------------------------------------------


def _mint_url(org):
    return reverse("orgs:invite_create", kwargs={"org_slug": org.slug})


def _mint_data(**overrides):
    data = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "link": "https://linkedin.com/in/janedoe",
        "image_url": "",
        "audience": "mentor",
        "kind": "org",
        "role": MembershipRole.MEMBER,
        "drafted_statement": "My drafted words.",
        "drafted_social_post": "",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_mint_invite_routes_through_doorway(client, admin_org, settings):
    """ONE flow: every invite is a doorway invite whenever a doorway is configured."""
    settings.DOORWAY_BASE_URL = "https://doorway.example/i/"
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_mint_url(org), _mint_data(), follow=True)
    assert resp.status_code == 200
    invite = Invite.objects.get(org=org)
    assert invite.name == "Jane Doe"
    assert invite.audience == "mentor"
    assert invite.drafted_statement == "My drafted words."
    assert invite.created_by == admin
    assert invite.doorway is True
    content = resp.content.decode()
    # The link lives in the invites ledger (copyable any time), not a flash message.
    assert f"https://doorway.example/i/{invite.code}/" in content
    assert _accept_url(invite) not in content


@pytest.mark.django_db
def test_mint_without_doorway_links_straight_to_accept(client, admin_org, settings):
    """Self-hosters with no doorway still get a working link — the accept door."""
    settings.DOORWAY_BASE_URL = ""
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_mint_url(org), _mint_data(), follow=True)
    invite = Invite.objects.get(org=org)
    assert invite.doorway is False
    assert _accept_url(invite) in resp.content.decode()


@pytest.mark.django_db
def test_mint_requires_name_or_email(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_mint_url(org), _mint_data(name="", email=""), follow=True)
    assert not Invite.objects.exists()
    message = str(list(resp.context["messages"])[0])
    assert "correct" in message.lower()


@pytest.mark.django_db
def test_members_page_lists_invite_statuses(client, admin_org, invite):
    org, admin = admin_org
    invite.mark_committed(claim_id=5)
    client.force_login(admin)
    resp = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Jane Doe" in content
    assert "committed" in content
    # Live invites keep a copyable link and a revoke control in the ledger.
    assert _accept_url(invite) in content
    assert (
        reverse("orgs:invite_revoke", kwargs={"org_slug": org.slug, "invite_id": invite.id})
        in content
    )


@pytest.mark.django_db
def test_s2s_accept_url_uses_public_base(client, admin_org, invite, settings):
    """The doorway relays accept_url to the invitee's browser: it must carry the
    public host even though the S2S call arrives over loopback."""
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    settings.PUBLIC_BASE_URL = "https://dash.workers.vc"
    org, _ = admin_org
    resp = client.get(
        f"/api/v1/orgs/{org.slug}/invites/{invite.code}/",
        HTTP_AUTHORIZATION=f"Bearer {S2S_TOKEN}",
    )
    assert resp.status_code == 200
    assert resp.json()["accept_url"] == (f"https://dash.workers.vc/invites/{invite.code}/accept/")


# --- The door (anonymous zero-friction accept) ----------------------------------------------


@pytest.mark.django_db
def test_door_renders_for_anonymous(client, invite):
    """A magic link with no session opens the door, not a login bounce."""
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Jane Doe, you're invited" in content
    assert invite.org.display_name in content
    assert invite.drafted_statement in content
    assert 'name="email"' not in content  # inviter supplied the email already
    assert client.session.get("pending_invite_code") == invite.code


@pytest.mark.django_db
def test_door_accept_creates_account_and_membership(client, invite):
    """One button: account minted from the invite email, membership materialized."""
    from django.contrib.auth import get_user_model

    resp = client.post(_accept_url(invite))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": invite.org.slug})
    user = get_user_model().objects.get(email="jane@example.com")
    assert user.display_name == "Jane Doe"
    assert not user.has_usable_password()
    assert Membership.objects.filter(org=invite.org, user=user).exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    # And they are signed in — the org dashboard actually loads.
    assert client.get(resp["Location"]).status_code == 200


@pytest.mark.django_db
def test_door_prompts_for_email_when_invite_has_none(client, admin_org):
    org, _ = admin_org
    # mentor audience: supporters never get a membership (golda 2026-07-22),
    # and this test asserts the membership materializes after the email door.
    invite = Invite.objects.create(
        org=org, role=MembershipRole.MEMBER, name="No Email", audience="mentor"
    )
    resp = client.get(_accept_url(invite))
    assert 'name="email"' in resp.content.decode()

    resp = client.post(_accept_url(invite))  # no email typed
    assert resp.status_code == 400
    assert not Membership.objects.filter(org=org).exclude(user__email="admin@example.com").exists()

    resp = client.post(_accept_url(invite), {"email": "typed@example.com"})
    assert resp.status_code == 302
    assert Membership.objects.filter(org=org, user__email="typed@example.com").exists()


@pytest.mark.django_db
def test_door_never_signs_into_existing_account(client, invite, user_factory):
    """Link possession must not unlock an account that already exists."""
    user_factory(email="jane@example.com")
    resp = client.post(_accept_url(invite))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("accounts:login")
    assert not Membership.objects.filter(org=invite.org, user__email="jane@example.com").exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.CREATED  # still live; they accept after signing in


@pytest.mark.django_db
def test_door_founder_accept_creates_venture_org(client, admin_org):
    from apps.orgs.models import Org

    org, _ = admin_org
    invite = Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience="founder",
        name="Fay Founder",
        email="fay@example.com",
        venture_name="Wayfern",
    )
    resp = client.post(_accept_url(invite))
    venture = Org.objects.get(display_name="Wayfern")
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": venture.slug})
    assert Membership.objects.filter(
        org=venture, user__email="fay@example.com", role=MembershipRole.ADMIN
    ).exists()


# --- Revoke ---------------------------------------------------------------------------------


def _revoke_url(invite):
    return reverse(
        "orgs:invite_revoke", kwargs={"org_slug": invite.org.slug, "invite_id": invite.id}
    )


@pytest.mark.django_db
def test_admin_revokes_live_invite(client, admin_org, invite):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_revoke_url(invite))
    assert resp.status_code == 302
    invite.refresh_from_db()
    assert invite.status == InviteStatus.REVOKED
    # The dead link bounces at the door.
    client.logout()
    resp = client.get(_accept_url(invite))
    assert resp["Location"] == reverse("orgs:landing")


@pytest.mark.django_db
def test_accepted_invite_cannot_be_revoked(client, admin_org, invite, user_factory):
    org, admin = admin_org
    user = user_factory()
    client.force_login(user)
    client.get(_accept_url(invite))  # accept
    client.logout()
    client.force_login(admin)
    client.post(_revoke_url(invite))
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED  # the join stands


@pytest.mark.django_db
def test_revoke_is_admin_only(client, admin_org, invite, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory()
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(_revoke_url(invite))
    assert resp.status_code == 403
    invite.refresh_from_db()
    assert invite.status == InviteStatus.CREATED


# --- mint_invite management command ------------------------------------------------


@pytest.mark.django_db
def test_mint_invite_command_prints_doorway_link(org_factory, settings, capsys):
    from django.core.management import call_command

    settings.DOORWAY_BASE_URL = "https://workers.vc/i/"
    org = org_factory(slug="integralmass")
    call_command(
        "mint_invite",
        "integralmass",
        "--name",
        "Jefferson Richards",
        "--role",
        "admin",
        "--audience",
        "founder",
    )
    out = capsys.readouterr().out.strip()
    from apps.orgs.models import Invite, InviteAudience, MembershipRole

    invite = Invite.objects.get(org=org)
    assert out == f"https://workers.vc/i/{invite.code}/"
    assert invite.role == MembershipRole.ADMIN
    assert invite.audience == InviteAudience.FOUNDER
    assert invite.name == "Jefferson Richards"
    assert invite.doorway is True
    assert invite.can_accept


@pytest.mark.django_db
def test_mint_invite_command_unknown_org_fails(settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("mint_invite", "nope", "--name", "X")


# --- Pool invites (kind=pool: screened applicants, never members) --------------------


def _pool_invite(org, **kwargs):
    from apps.orgs.models import InviteKind

    defaults = dict(
        org=org,
        kind=InviteKind.POOL,
        audience="supporter",
        name="Walk Up",
        email="walkup@example.com",
    )
    defaults.update(kwargs)
    return Invite.objects.create(**defaults)


@pytest.mark.django_db
def test_pool_accept_joins_no_org_and_creates_none(client, admin_org, user_factory, monkeypatch):
    """The applicant-pool contract (golda 2026-07-20): accepting a pool invite
    records the person (accepted + accepted_by) and NOTHING else — no
    membership, no org, and no provisioning report to amebo."""
    from apps.orgs import invites as invites_module
    from apps.orgs.models import Org

    reported = []
    monkeypatch.setattr(invites_module, "provision_membership", lambda *a, **k: reported.append(a))
    org, _ = admin_org
    invite = _pool_invite(org)
    orgs_before = Org.objects.count()
    user = user_factory(email="walkup@example.com")
    client.force_login(user)

    resp = client.get(_accept_url(invite))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:landing")  # no COHORT_POOL_LANDING set
    assert not Membership.objects.filter(user=user).exists()
    assert Org.objects.count() == orgs_before
    assert reported == []
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.accepted_by == user


@pytest.mark.django_db
def test_pool_accept_lands_on_cohort_pool_landing(client, admin_org, user_factory, settings):
    settings.COHORT_POOL_LANDING = "https://workers.vc/pool/"
    org, _ = admin_org
    invite = _pool_invite(org)
    user = user_factory()
    client.force_login(user)
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 302
    assert resp["Location"] == "https://workers.vc/pool/"


@pytest.mark.django_db
def test_pool_invite_is_single_use(client, admin_org, user_factory):
    org, _ = admin_org
    invite = _pool_invite(org)
    first = user_factory()
    client.force_login(first)
    client.get(_accept_url(invite))

    client.logout()
    second = user_factory()
    client.force_login(second)
    resp = client.get(_accept_url(invite))
    assert resp["Location"] == reverse("orgs:landing")
    invite.refresh_from_db()
    assert invite.accepted_by == first


@pytest.mark.django_db
def test_pool_invite_preview_by_existing_member_does_not_burn(client, admin_org):
    """The inviter checking their own pool link is previewing, not applying."""
    org, admin = admin_org
    invite = _pool_invite(org)
    client.force_login(admin)
    client.get(_accept_url(invite))
    invite.refresh_from_db()
    assert invite.status == InviteStatus.CREATED
    assert invite.accepted_by is None


@pytest.mark.django_db
def test_mint_invite_command_pool_flag(org_factory, capsys):
    from django.core.management import call_command

    from apps.orgs.models import InviteKind

    org = org_factory(slug="vc")
    call_command("mint_invite", "vc", "--name", "Walk Up", "--pool")
    invite = Invite.objects.get(org=org)
    assert invite.kind == InviteKind.POOL


@pytest.mark.django_db
def test_mint_invite_command_pool_refuses_venture(org_factory):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    org_factory(slug="vc")
    with pytest.raises(CommandError):
        call_command("mint_invite", "vc", "--name", "X", "--pool", "--venture-name", "Y")


@pytest.mark.django_db
def test_pool_accept_after_login_lands_on_pool_landing(client, admin_org, user_factory, settings):
    """The stash-code-then-login path must land pool accepts on the dash too,
    never a GovKit page (golda 2026-07-21)."""
    settings.GOVKIT_DEV_LOGIN = True
    settings.COHORT_POOL_LANDING = "https://workers.vc/dash/"
    org, _ = admin_org
    invite = _pool_invite(org)
    client.get(_accept_url(invite))  # anonymous: code stashed in session
    assert client.session.get("pending_invite_code") == invite.code
    user = user_factory(email="walkup@example.com")
    resp = client.post(
        reverse("accounts:dev_login"), {"email": "walkup@example.com", "password": "pw12345!"}
    )
    assert resp.status_code == 302
    assert resp["Location"] == "https://workers.vc/dash/"
    assert not Membership.objects.filter(user=user).exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.accepted_by == user


@pytest.mark.django_db
def test_admin_mints_pool_invite_from_members_page(client, admin_org):
    from apps.orgs.models import InviteKind

    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {"name": "Walk Up", "audience": "supporter", "kind": "pool", "role": "member"},
    )
    assert resp.status_code == 302
    invite = Invite.objects.get(org=org, name="Walk Up")
    assert invite.kind == InviteKind.POOL


@pytest.mark.django_db
def test_members_page_pool_invite_refuses_venture(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {
            "name": "Walk Up",
            "audience": "founder",
            "kind": "pool",
            "role": "member",
            "venture_name": "Contradiction Inc",
        },
    )
    assert not Invite.objects.filter(org=org, name="Walk Up").exists()
