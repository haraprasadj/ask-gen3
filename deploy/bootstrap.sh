#!/usr/bin/env bash
# One-time AWS setup for ask-gen3 (ADR-0006). Idempotent — safe to re-run.
#
#   aws login && ./deploy/bootstrap.sh
#
# Creates: ECR repository, Lambda execution role, the function itself, a public
# Function URL in RESPONSE_STREAM mode, and the GitHub OIDC deploy role. Reads
# OPENROUTER_API_KEY from .env and sets it as an encrypted Lambda env var; the
# value is never printed.
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
NAME=${NAME:-ask-gen3}
REPO=${REPO:-uc-cdis-community/ask-gen3}   # owner/repo allowed to deploy
MEMORY=${MEMORY:-2048}                     # >1769 MB buys a full vCPU (ADR-0006)
TIMEOUT=${TIMEOUT:-120}

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$NAME"
say() { printf '\n== %s\n' "$*"; }

say "account $ACCOUNT, region $REGION"

say "ECR repository"
aws ecr describe-repositories --repository-names "$NAME" --region "$REGION" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name "$NAME" --region "$REGION" \
    --image-scanning-configuration scanOnPush=true >/dev/null
# Images are ~1 GB each; without this every weekly index build accumulates.
aws ecr put-lifecycle-policy --repository-name "$NAME" --region "$REGION" \
  --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"keep last 3",
    "selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},
    "action":{"type":"expire"}}]}' >/dev/null

say "Lambda execution role"
aws iam get-role --role-name "$NAME-exec" >/dev/null 2>&1 || {
  aws iam create-role --role-name "$NAME-exec" --assume-role-policy-document '{
    "Version":"2012-10-17","Statement":[{"Effect":"Allow",
      "Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$NAME-exec" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  sleep 10   # IAM is eventually consistent; Lambda rejects the role until it propagates
}
ROLE=$(aws iam get-role --role-name "$NAME-exec" --query Role.Arn --output text)

say "function"
if ! aws lambda get-function --function-name "$NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "No function yet. Push an image first:"
  echo "  aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR"
  echo "  just index && just docker"
  echo "  docker tag ask-gen3 $ECR:bootstrap && docker push $ECR:bootstrap"
  echo "Then re-run this script."
  aws ecr describe-images --repository-name "$NAME" --region "$REGION" >/dev/null 2>&1 || exit 1
  TAG=$(aws ecr describe-images --repository-name "$NAME" --region "$REGION" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' --output text)
  [ "$TAG" != "None" ] || exit 1
  aws lambda create-function --function-name "$NAME" --region "$REGION" \
    --package-type Image --code ImageUri="$ECR:$TAG" --role "$ROLE" \
    --architectures arm64 --memory-size "$MEMORY" --timeout "$TIMEOUT" >/dev/null
  aws lambda wait function-active-v2 --function-name "$NAME" --region "$REGION"
fi

say "secret"
if [ -f .env ] && grep -q '^OPENROUTER_API_KEY=.' .env; then
  KEY=$(grep '^OPENROUTER_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  aws lambda update-function-configuration --function-name "$NAME" --region "$REGION" \
    --environment "Variables={OPENROUTER_API_KEY=$KEY,PUBLIC_URL=https://$NAME}" >/dev/null
  echo "set from .env (encrypted at rest by Lambda's default KMS key)"
else
  echo "no OPENROUTER_API_KEY in .env — set it yourself before the function will answer"
fi

say "public Function URL, streaming"
aws lambda get-function-url-config --function-name "$NAME" --region "$REGION" >/dev/null 2>&1 || {
  aws lambda create-function-url-config --function-name "$NAME" --region "$REGION" \
    --auth-type NONE --invoke-mode RESPONSE_STREAM >/dev/null
  aws lambda add-permission --function-name "$NAME" --region "$REGION" \
    --statement-id public-url --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
}

say "GitHub OIDC deploy role"
aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com" \
  >/dev/null 2>&1 ||
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
aws iam get-role --role-name "$NAME-deploy" >/dev/null 2>&1 || {
  aws iam create-role --role-name "$NAME-deploy" --assume-role-policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Federated":"arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"},
  "Action":"sts:AssumeRoleWithWebIdentity",
  "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com"},
               "StringLike":{"token.actions.githubusercontent.com:sub":"repo:$REPO:*"}}}]}
JSON
)" >/dev/null
  aws iam put-role-policy --role-name "$NAME-deploy" --policy-name deploy --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:CompleteLayerUpload",
   "ecr:InitiateLayerUpload","ecr:PutImage","ecr:UploadLayerPart","ecr:BatchGetImage"],
  "Resource":"arn:aws:ecr:$REGION:$ACCOUNT:repository/$NAME"},
 {"Effect":"Allow","Action":["lambda:UpdateFunctionCode","lambda:GetFunction",
   "lambda:PublishVersion"],"Resource":"arn:aws:lambda:$REGION:$ACCOUNT:function:$NAME"}]}
JSON
)"
}

URL=$(aws lambda get-function-url-config --function-name "$NAME" --region "$REGION" \
  --query FunctionUrl --output text)
cat <<OUT

done.
  url          $URL
  image        $ECR
  deploy role  arn:aws:iam::$ACCOUNT:role/$NAME-deploy

Set these as GitHub Actions repository variables:
  gh variable set AWS_ROLE_ARN --body arn:aws:iam::$ACCOUNT:role/$NAME-deploy
  gh variable set AWS_REGION --body $REGION

Check it:
  curl $URL/healthz
OUT
