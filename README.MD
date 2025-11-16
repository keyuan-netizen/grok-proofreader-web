## ENV CREATION STEPS
# From your project folder
cd /Users/keyuan/Documents/keith/USCIS-keyuan/automations/grok-proofreader-web

# Create venv
python3 -m venv venv

# Activate it
source venv/bin/activate

# one-liner to run
source venv/bin/activate && pip install -r requirements.txt python-dotenv &> /dev/null && python app.py

## Init Git Commit
echo "# grok-proofreader-web" >> README.md
git init
git add README.md
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/keyuan-netizen/grok-proofreader-web.git
git push -u origin main
# grok-proofreader-web

## commit changes
git status                  # optional: review current changes
git add app.py proofreader.py templates/index.html .gitignore requirements.txt README.MD render.yaml
git commit -m "Add per-file downloads and queue cleanup"
git push origin main
