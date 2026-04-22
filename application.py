from app import app, init_db

# Elastic Beanstalk expects 'application' as the WSGI callable
application = app

# Run DB migrations/seed on every startup (idempotent — safe with multiple workers)
with app.app_context():
    init_db()

if __name__ == '__main__':
    application.run()
