FROM node:20-alpine AS build

WORKDIR /app

COPY apps/frontend/package*.json ./
RUN npm install

COPY apps/frontend /app
RUN npm run build

FROM node:20-alpine

WORKDIR /app

RUN npm install -g serve
COPY --from=build /app/dist /app/dist

EXPOSE 5173

CMD ["serve", "-s", "dist", "-l", "5173"]
