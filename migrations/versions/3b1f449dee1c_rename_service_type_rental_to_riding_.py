"""rename service_type rental to riding (data migration)

Revision ID: 3b1f449dee1c
Revises: 2183ce478c90
Create Date: 2026-08-08 02:05:23.367097

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3b1f449dee1c'
down_revision = '2183ce478c90'
branch_labels = None
depends_on = None


def upgrade():
    # تحديث بيانات فقط: القيمة القديمة "rental" (إيجار يومي) أصبحت "riding" (ركوب) بعد إعادة
    # تسمية أنواع الخدمة — لولا هذا التحديث، أي حصان محفوظ بالقيمة القديمة يختفي بصمت من قوائم
    # الحجز المتاحة للزوار (الفلترة الجديدة تبحث عن "riding" لا "rental").
    op.execute("UPDATE horses SET service_type = 'riding' WHERE service_type = 'rental'")


def downgrade():
    op.execute("UPDATE horses SET service_type = 'rental' WHERE service_type = 'riding'")
