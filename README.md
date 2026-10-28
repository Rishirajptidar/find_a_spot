# Find-A-Spot

Find-A-Spot is a Flask-based smart parking web application that helps users discover parking locations, book slots, manage profiles, and view parking activity through role-based dashboards.

## Main Features

- User registration and login with OTP email verification
- Role-based access for customer, admin, and parking manager
- Parking slot discovery and booking workflow
- Live parking information API and dashboard views
- Profile management and payment history tracking
- Admin tools for parking lot management, user management, and reports
- Email support/contact workflow
- TomTom map integration for location search and nearby parking

## Tech Stack

- Python 3
- Flask
- Flask-MySQLdb
- Flask-Mail
- MySQL/MariaDB
- Jinja2 templates
- HTML/CSS/JavaScript

## Project Structure

```text
myproject/
├── flask1.py
├── templates/
├── static/
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── .venv/              # local virtual environment (not tracked)
├── env/                # local virtual environment (not tracked)
└── .env                # local secrets (not tracked)
```

## Prerequisites

- Python 3.10+
- MySQL or MariaDB server
- Existing database schema used by the application
- SMTP account/app password for OTP and support mail

## Installation

1. Open a terminal in the project directory.
2. Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

## Environment Variable Setup

1. Create a local `.env` file in the project root (`myproject/.env`).
2. Copy variable names from `.env.example`.
3. Fill in real values locally (never commit `.env`).

Required variables:

- `SECRET_KEY`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DB`
- `MAIL_SERVER`
- `MAIL_PORT`
- `MAIL_USE_TLS`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `TOMTOM_API_KEY`
- `SUPPORT_RECIPIENT_EMAIL`

## Database Setup

1. Create/select your MySQL/MariaDB database.
2. Ensure the required tables used by `flask1.py` already exist.
3. Set matching DB connection values in `.env`.

## Run Locally

From project root:

```powershell
.\.venv\Scripts\Activate.ps1
python flask1.py
```

Then open the local URL printed by Flask (typically `http://127.0.0.1:5000`).

## Important Security Note

- Do not commit `.env`.
- Keep all secrets (Flask secret key, DB credentials, SMTP credentials, API keys) in environment variables only.
- Commit only `.env.example` with placeholder values.

## Future Improvements

- Add automated tests for routes and OTP workflows
- Move app startup to `if __name__ == "__main__":` guard
- Add production WSGI entrypoint and deployment docs
- Add schema migration management (Alembic/Flask-Migrate)
- Add CI checks for linting and tests
