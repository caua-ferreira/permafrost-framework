@echo off
echo Removendo lock do git...
if exist .git\index.lock del /f .git\index.lock
echo Configurando git...
git config user.email "caua.fer@gmail.com"
git config user.name "Caua Ferreira"
echo Fazendo commit de tudo e push...
git add .
git commit -m "fix: corrige links de idiomas no README para URLs absolutas do GitHub"
git push
echo Criando e publicando tag v1.1.2...
git tag v1.1.2
git push origin v1.1.2
echo.
echo Pronto! v1.1.2 publicada. Pode fechar essa janela.
pause
