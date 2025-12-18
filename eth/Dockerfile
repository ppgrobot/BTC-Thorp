# ETH Lambda Functions Docker Image
# Supports: ETHPriceCollector and ETHVolatilityAPI
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy Lambda function code
COPY lambda_package/*.py ${LAMBDA_TASK_ROOT}/

# Default handler (override with --function-handler when creating Lambda)
CMD ["eth_price_collector.lambda_handler"]
