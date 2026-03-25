from fastapi import HTTPException, Response
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, PendingRegistration, User, UserRecoveryCode
from app.routers import auth
from app.schemas import RegistrationCompleteRequest, UserRegisterRequest


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_register_creates_user_with_normalized_identity_and_totp_enrollment():
    with _build_session() as db:
        start = auth.register(
            UserRegisterRequest(
                username="  trader-one  ",
                email="Trader.One@Example.com",
                password="StrongPass123!",
            ),
            db=db,
        )

        pending = db.query(PendingRegistration).one()
        assert pending.username == "trader-one"
        assert pending.email == "trader.one@example.com"

        raw_response = Response()
        complete = auth.register_complete(
            RegistrationCompleteRequest(
                registration_token=start.registration_token,
                otp_code=pyotp.TOTP(start.otp_secret).now(),
            ),
            db=db,
            response=raw_response,
        )

        assert complete.user.username == "trader-one"
        assert complete.user.email == "trader.one@example.com"
        assert complete.authenticated is True
        assert complete.enrollment_required is False
        assert len(complete.recovery_codes) > 0
        stored = db.query(User).filter(User.username == "trader-one").one()
        assert stored.role == "user"
        assert stored.totp_secret_encrypted
        assert db.query(PendingRegistration).count() == 0
        assert db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id == stored.id).count() == len(
            complete.recovery_codes
        )


def test_register_rejects_duplicate_username_or_email_case_insensitive():
    with _build_session() as db:
        first = auth.register(
            UserRegisterRequest(
                username="trader-one",
                email="trader.one@example.com",
                password="StrongPass123!",
            ),
            db=db,
        )
        auth.register_complete(
            RegistrationCompleteRequest(
                registration_token=first.registration_token,
                otp_code=pyotp.TOTP(first.otp_secret).now(),
            ),
            db=db,
            response=Response(),
        )

        try:
            auth.register(
                UserRegisterRequest(
                    username="trader-one",
                    email="TRADER.ONE@example.com",
                    password="AnotherStrongPass123!",
                ),
                db=db,
            )
            raise AssertionError("Expected duplicate registration to fail")
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "already exists" in str(exc.detail)
