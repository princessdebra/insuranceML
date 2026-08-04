echo "Building the app...."
echo "Building the System"
npm run build
echo "Deploying the app to server...."
scp -r dist/* xeai@41.90.122.129:/var/www/medinsure/

echo "Deployment completed successfully! :)"