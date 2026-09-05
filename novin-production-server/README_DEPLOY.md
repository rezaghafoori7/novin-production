این پوشه شامل یک نمونهٔ سادهٔ API برای مدیریت "کدهای دسترسی" است و به شما اجازه می‌دهد تا صفحهٔ frontend را همراه با داده‌های م��ترک و پایدار روی یک سرور اجرا کنید.

راهنمای سریع اجرا (لوکال):

1. وارد پوشهٔ novin-production-server شوید:
   cd novin-production-server

2. ساخت virtualenv و نصب وابستگی‌ها:
   python -m venv .venv
   source .venv/bin/activate   # ویندوز: .venv\Scripts\activate
   pip install -r requirements.txt

3. اجرای سرور:
   uvicorn app:app --reload --host 0.0.0.0 --port 8000

4. باز کردن در مرورگر:
   http://localhost:8000/  (صفحهٔ دسترسی و مدیریت)

توضیحات استقرار (Deploy):
- می‌توانید این اپ را در پلتفرم‌هایی مانند Railway, Render, Heroku یا Fly.io مستقر کنید. فقط پوشهٔ novin-production-server را به ریموت پوش کنید و سرویس را اجرا کنید (در Render/Railway یک سرویس web ایجاد کنید و دستور اجرا را از Procfile استفاده کنید).
- بعد از deploy، صفحهٔ اصلی (مثل https://your-app.onrender.com/) صفحهٔ frontend را نمایش می‌دهد و API در مسیر /api/* در دسترس خواهد بود.

نکات امنیتی:
- در نمونهٔ حاضر، رمز مدیریت به‌صورت محلی در frontend بررسی می‌شود (admin-novin-2026). برای محیط تولید باید احراز هویت مناسب و مدیریت رمزها را در سرور قرار دهید.
- داده‌ها در فایل JSON داخل repo ذخیره می‌شوند (novin-production-server/data/codes.json). برای استفادهٔ پایدار و مقیاس‌پذیر توصیه می‌شود از دیتابیس (Postgres, SQLite, etc.) استفاده کنید.

اگر می‌خواهید، من می‌توانم:
- راه‌اندازی و اتصال اتوماتیک به یک سرویس رایگان (Railway/Render) را راهنمایی یا تنظیم کنم.
- احراز هویت سادهٔ مبتنی بر رمز عبورِ مدیر را به سرور منتقل کنم.
- لاگ‌ها و مشاهدهٔ سوابق ورود را گسترش دهم.
