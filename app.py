from flask import Flask, render_template, request
from models import db, StartupCase

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///startups_html.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    search_query = request.args.get('q', '')
    active_tag = request.args.get('tag', '')

    # Базовый запрос
    query = StartupCase.query

    if search_query:
        query = query.filter(
            StartupCase.title.ilike(f'%{search_query}%') | 
            StartupCase.description.ilike(f'%{search_query}%')
        )
    if active_tag:
        query = query.filter(StartupCase.category == active_tag)

    cases = query.order_by(StartupCase.id.desc()).all()
    
    # Получаем список уникальных тегов для бокового меню
    all_tags = db.session.query(StartupCase.category).distinct().all()
    tags = [t[0] for t in all_tags if t[0]]

    return render_template(
        'index.html', 
        cases=cases, 
        search_query=search_query, 
        active_tag=active_tag, 
        tags=tags
    )

@app.route('/case/<int:case_id>')
def case_detail(case_id):
    case = StartupCase.query.get_or_404(case_id)
    return render_template('case.html', case=case)

if __name__ == '__main__':
    app.run(debug=True, port=5000)