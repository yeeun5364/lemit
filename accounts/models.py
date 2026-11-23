from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

# 시연용 데모 계정 이메일 (seed_demo 전용, #24). example.com은 문서·예시용 예약 도메인이라 실제 메일이 가지 않는다.
# 회원가입에서는 받지 않는다 (PR #65 리뷰).
DEMO_EMAIL = "demo@example.com"
# 시연용 데모 수강생 계정 (#80). seed_demo가 만들어 데모 강사의 수강생 '김하늘' 기록에 연결한다
DEMO_STUDENT_EMAIL = "student@example.com"
RESERVED_EMAILS = {DEMO_EMAIL, DEMO_STUDENT_EMAIL}


class UserManager(BaseUserManager):
    """username 대신 email을 로그인 ID로 쓰는 매니저."""

    use_in_migrations = True

    def get_by_natural_key(self, email):
        """로그인 시 이메일 대소문자를 구분하지 않는다 (Teacher@x.com == teacher@x.com)."""
        return self.get(email__iexact=email)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("이메일은 필수입니다.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("관리자는 is_staff=True 여야 합니다.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("관리자는 is_superuser=True 여야 합니다.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """강사 계정. 이메일로 로그인한다 (ERD 2-4)."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField("이메일", unique=True)
    name = models.CharField("이름", max_length=50)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    objects = UserManager()

    class Meta:
        verbose_name = "강사"
        verbose_name_plural = "강사"
        constraints = [
            # 대소문자만 다른 같은 이메일로 두 번 가입하지 못하게 DB에서도 막는다 (로그인이 대소문자 무시이므로 필수)
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
                violation_error_message="이미 가입된 이메일입니다.",
            ),
        ]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class LoginAttempt(models.Model):
    """로그인 실패·비밀번호 재설정 요청 기록 (시도 횟수 제한, accounts/throttle.py, #7).

    계정과 연결하지 않고 입력한 이메일(소문자)과 접속 IP만 남긴다. 없는 이메일도 같은 방식으로 세어
    가입 여부가 드러나지 않게 한다. 하루가 지난 기록은 새 기록을 남길 때 지운다.
    로그인 시도는 비밀번호 확인 전에 행을 먼저 만들고(예약), 성공하면 그 행을 지운다.
    """

    class Kind(models.TextChoices):
        LOGIN = "login", "로그인 실패"
        RESET = "reset", "비밀번호 재설정 요청"

    kind = models.CharField("종류", max_length=10, choices=Kind.choices)
    email = models.CharField("입력한 이메일", max_length=254, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    # 로그인 성공 후 이메일 집계에서만 뺀다. 행은 남아 IP 집계에는 계속 들어간다 (PR #70 리뷰 P2)
    email_cleared = models.BooleanField("이메일 집계 제외", default=False)
    # 비밀번호 확인 중인 로그인 예약. 실패로 끝나면 False(확정된 실패), 성공하면 행을 지운다.
    # 진행 중인 예약은 다른 요청의 로그인 성공으로 초기화되지 않는다 (PR #70 재검토 P2)
    pending = models.BooleanField("확인 중", default=False)
    created_at = models.DateTimeField("시각", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "로그인 시도"
        verbose_name_plural = "로그인 시도"
        indexes = [
            models.Index(fields=["kind", "email", "created_at"], name="attempt_kind_email"),
            models.Index(fields=["kind", "ip", "created_at"], name="attempt_kind_ip"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.email or '-'} {self.created_at:%Y-%m-%d %H:%M}"
