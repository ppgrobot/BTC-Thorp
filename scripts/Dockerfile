# Cleanup Lambda Function Docker Image
# Supports: PriceHistoryCleanup
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy Lambda function code
COPY price_history_cleanup.py ${LAMBDA_TASK_ROOT}/

# Default handler
CMD ["price_history_cleanup.lambda_handler"]
