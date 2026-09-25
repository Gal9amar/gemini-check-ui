# Jio Gemini Activation Scanner — Web UI

## מבנה
- `scanner.py` — הסורק: טוען פאנלים, סורק מכשירים מחוברים, מאתר מספרים ומפעיל אותם.
- `app.py` — שרת Flask: מפעיל את הסורק בתהליך נפרד, מנהל את מסד הנתונים (פאנלים/תוצאות), וחושף API + ממשק.
- `templates/index.html` — ממשק RTL.
- `static/style.css` — עיצוב Dark Modern SaaS.
- `static/app.js` — כל לוגיקת הממשק: לוח בקרה, ניהול Panels (כולל הוספה מטקסט חופשי, בדיקת זמינות, רשימת "לבדיקה"), לינקים תקינים, וצפייה במסד הנתונים.
- `gemini_results.csv` / `gemini_activation_links.txt` — נכתבים בזמן אמת על ידי הסורק בכל ריצה; `app.py` מסנכרן מהם שורות חדשות למסד הנתונים.

## מסד נתונים
כל הפאנלים והתוצאות מנוהלים ב-SQLite (`scanner.db`), ולא בקובצי טקסט. שני מצבי הפעלה:

- **מקומי (ברירת מחדל)** — קובץ `scanner.db` בתיקיית הפרויקט.
- **Turso (ענן)** — אם מוגדרים משתני הסביבה `TURSO_DATABASE_URL` ו-`TURSO_AUTH_TOKEN`, האפליקציה מתחברת אוטומטית למסד בענן במקום לקובץ המקומי. שימושי לפריסה בשירותים כמו Render, שבהם הדיסק המקומי לא בהכרח נשמר בין דיפלוי לדיפלוי.

ניתן להגדיר אותם בקובץ `.env` בתיקיית הפרויקט (לא נכנס ל-git):
```
TURSO_DATABASE_URL=libsql://your-db-name.turso.io
TURSO_AUTH_TOKEN=your-token
```

## הרשאת גישה
שתי שיטות הגנה, לפי סדר עדיפות:

1. **התחברות בקוד למייל (מומלץ)** — אם מוגדרים `GMAIL_ADDRESS` ו-`GMAIL_APP_PASSWORD`, מסך ההתחברות לא מציג ולא מבקש כתובת מייל בכלל: לוחצים "שליחת קוד", מקבלים קוד בן 6 ספרות לתיבה שב-`GMAIL_ADDRESS`, מזינים אותו, ומתחברים. הקוד בתוקף ל-10 דקות, ומוגבל ל-5 ניסיונות. `GMAIL_APP_PASSWORD` הוא **App Password** בן 16 תווים (Google Account → Security → App Passwords — דורש אימות דו-שלבי מופעל בחשבון), **לא** הסיסמה הרגילה שלך.
2. **Basic Auth** — אם `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` לא מוגדרים אבל `APP_PASSWORD` כן, חוזר להתנהגות הישנה (שם משתמש מ-`APP_USERNAME`, ברירת מחדל `admin`).

בלי אף אחד מהם הממשק פתוח לגמרי — מתאים לפיתוח מקומי בלבד. **חובה להגדיר אחת מהשיטות לפני חשיפת השרת לאינטרנט.**

גם מומלץ להגדיר `SECRET_KEY` (מחרוזת אקראית כלשהי) — משמש לחתימת עוגיית ההתחברות; בלעדיו נוצר מפתח אקראי חדש בכל הפעלה מחדש של השרת, כלומר כולם יתנתקו בכל דיפלוי.

## התקנה מקומית (Windows)
```powershell
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
py app.py
```
פתח בדפדפן: `http://127.0.0.1:5000`

## דפלוי ל-Render
1. חבר את הריפו ב-GitHub לחשבון Render, וצור **Web Service** חדש (לא Static Site).
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app --workers 1 --bind 0.0.0.0:$PORT`
4. משתני סביבה נדרשים (בממשק Render, לא בקובץ):
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` — כדי שהנתונים ישרדו בין דיפלויים.
   - `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (או לחלופין `APP_USERNAME`/`APP_PASSWORD`) — כדי להגן על הממשק.
   - `SECRET_KEY` — כדי שההתחברות לא תתנתק בכל דיפלוי.
5. האפליקציה מאזינה אוטומטית לפורט שרנדר מזריקה (`$PORT`), אין צורך בהגדרה נוספת.
