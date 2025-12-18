#!/bin/bash
# Deploy Dashboard to S3 + CloudFront

set -e

REGION="us-east-1"
BUCKET_NAME="btc-trading-dashboard-$(aws sts get-caller-identity --query Account --output text)"

echo "=========================================="
echo "Deploying BTC Dashboard to AWS"
echo "=========================================="

# Navigate to dashboard directory
cd "$(dirname "$0")/.."

# Step 1: Create S3 bucket if it doesn't exist
echo ""
echo "Step 1: Setting up S3 bucket..."

if aws s3api head-bucket --bucket $BUCKET_NAME --region $REGION 2>/dev/null; then
    echo "Bucket $BUCKET_NAME already exists"
else
    echo "Creating bucket $BUCKET_NAME..."
    aws s3api create-bucket \
        --bucket $BUCKET_NAME \
        --region $REGION
fi

# Step 2: Configure bucket for static website hosting
echo ""
echo "Step 2: Configuring static website hosting..."

aws s3api put-bucket-website \
    --bucket $BUCKET_NAME \
    --website-configuration '{
        "IndexDocument": {"Suffix": "index.html"},
        "ErrorDocument": {"Key": "index.html"}
    }'

# Step 3: Set bucket policy to allow public read (for CloudFront OAC)
echo ""
echo "Step 3: Setting bucket policy..."

# First, disable block public access for the bucket
aws s3api put-public-access-block \
    --bucket $BUCKET_NAME \
    --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

# Set bucket policy for public read
aws s3api put-bucket-policy \
    --bucket $BUCKET_NAME \
    --policy "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [
            {
                \"Sid\": \"PublicReadGetObject\",
                \"Effect\": \"Allow\",
                \"Principal\": \"*\",
                \"Action\": \"s3:GetObject\",
                \"Resource\": \"arn:aws:s3:::$BUCKET_NAME/*\"
            }
        ]
    }"

# Step 4: Upload dashboard files
echo ""
echo "Step 4: Uploading dashboard files..."

aws s3 sync . s3://$BUCKET_NAME/ \
    --exclude "*.sh" \
    --exclude "scripts/*" \
    --exclude "api/*" \
    --exclude ".api_endpoint" \
    --content-type "text/html" \
    --cache-control "max-age=300"

# Step 5: Create CloudFront distribution if it doesn't exist
echo ""
echo "Step 5: Setting up CloudFront..."

# Check if distribution exists
EXISTING_DIST=$(aws cloudfront list-distributions --query "DistributionList.Items[?Origins.Items[0].DomainName=='$BUCKET_NAME.s3.amazonaws.com'].Id" --output text 2>/dev/null || echo "")

if [ -z "$EXISTING_DIST" ] || [ "$EXISTING_DIST" == "None" ]; then
    echo "Creating new CloudFront distribution..."

    DIST_CONFIG=$(cat <<EOF
{
    "CallerReference": "btc-dashboard-$(date +%s)",
    "Origins": {
        "Quantity": 1,
        "Items": [
            {
                "Id": "S3-$BUCKET_NAME",
                "DomainName": "$BUCKET_NAME.s3.amazonaws.com",
                "S3OriginConfig": {
                    "OriginAccessIdentity": ""
                }
            }
        ]
    },
    "DefaultCacheBehavior": {
        "TargetOriginId": "S3-$BUCKET_NAME",
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": {
            "Quantity": 2,
            "Items": ["GET", "HEAD"],
            "CachedMethods": {
                "Quantity": 2,
                "Items": ["GET", "HEAD"]
            }
        },
        "ForwardedValues": {
            "QueryString": false,
            "Cookies": {"Forward": "none"}
        },
        "MinTTL": 0,
        "DefaultTTL": 300,
        "MaxTTL": 86400,
        "Compress": true
    },
    "DefaultRootObject": "index.html",
    "Enabled": true,
    "Comment": "BTC Trading Dashboard",
    "PriceClass": "PriceClass_100"
}
EOF
)

    DIST_RESULT=$(aws cloudfront create-distribution \
        --distribution-config "$DIST_CONFIG" \
        --output json)

    DIST_ID=$(echo $DIST_RESULT | python3 -c "import sys, json; print(json.load(sys.stdin)['Distribution']['Id'])")
    DIST_DOMAIN=$(echo $DIST_RESULT | python3 -c "import sys, json; print(json.load(sys.stdin)['Distribution']['DomainName'])")

    echo "Created distribution: $DIST_ID"
else
    DIST_ID=$EXISTING_DIST
    DIST_DOMAIN=$(aws cloudfront get-distribution --id $DIST_ID --query "Distribution.DomainName" --output text)
    echo "Using existing distribution: $DIST_ID"

    # Invalidate cache
    echo "Invalidating CloudFront cache..."
    aws cloudfront create-invalidation \
        --distribution-id $DIST_ID \
        --paths "/*" > /dev/null
fi

# Save dashboard URL
DASHBOARD_URL="https://$DIST_DOMAIN"
echo "$DASHBOARD_URL" > .dashboard_url

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "S3 Bucket: $BUCKET_NAME"
echo "CloudFront Distribution: $DIST_ID"
echo ""
echo "Dashboard URL: $DASHBOARD_URL"
echo ""
echo "Note: CloudFront may take 5-10 minutes to fully deploy."
echo "The S3 website URL is available immediately:"
echo "  http://$BUCKET_NAME.s3-website-$REGION.amazonaws.com"
echo ""
echo "URL saved to: dashboard/.dashboard_url"
