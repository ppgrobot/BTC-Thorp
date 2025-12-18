# Weather Lambda Function Docker Image
# Supports: KalshiWeatherTradingBot
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy Lambda function code
COPY lambda_package/*.py ${LAMBDA_TASK_ROOT}/

# Default handler
CMD ["lambda_function.lambda_handler"]
