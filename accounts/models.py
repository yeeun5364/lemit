from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """username 대신 email을 로그인 ID로 쓰는 매니저."""

    use_in_migrations = True

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

    def __str__(self):
        return f"{self.name} <{self.email}>"
