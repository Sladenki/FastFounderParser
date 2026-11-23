from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class StartupCase(db.Model):
    __tablename__ = 'startup_cases'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(100), nullable=True)