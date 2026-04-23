FROM node:20-alpine

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.3 --activate

COPY package.json ./
COPY pnpm-lock.yaml* ./
RUN pnpm install

COPY . .

EXPOSE 5173

CMD ["pnpm", "dev"]
