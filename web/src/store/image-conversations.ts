"use client";

import localforage from "localforage";

import type { ImageModel } from "@/lib/api";

export type StoredImage = {
  id: string;
  status?: "loading" | "success" | "error";
  b64_json?: string;
  error?: string;
};

export type ImageConversationStatus = "generating" | "success" | "error";

export type ImageConversation = {
  id: string;
  title: string;
  prompt: string;
  model: ImageModel;
  count: number;
  images: StoredImage[];
  createdAt: string;
  status: ImageConversationStatus;
  error?: string;
  referenceImage?: string; // base64 data URL of the reference image
};

const imageConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "image_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";

function normalizeStoredImage(image: StoredImage): StoredImage {
  if (image.status === "loading" || image.status === "error" || image.status === "success") {
    return image;
  }
  return {
    ...image,
    status: image.b64_json ? "success" : "loading",
  };
}

function normalizeConversation(conversation: ImageConversation): ImageConversation {
  return {
    ...conversation,
    images: (conversation.images || []).map(normalizeStoredImage),
  };
}

export async function listImageConversations(): Promise<ImageConversation[]> {
  const items = (await imageConversationStorage.getItem<ImageConversation[]>(IMAGE_CONVERSATIONS_KEY)) || [];
  return items.map(normalizeConversation).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function saveImageConversation(conversation: ImageConversation): Promise<void> {
  const items = await listImageConversations();
  const nextItems = [normalizeConversation(conversation), ...items.filter((item) => item.id !== conversation.id)];
  nextItems.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  await imageConversationStorage.setItem(IMAGE_CONVERSATIONS_KEY, nextItems);
}

export async function deleteImageConversation(id: string): Promise<void> {
  const items = await listImageConversations();
  await imageConversationStorage.setItem(
    IMAGE_CONVERSATIONS_KEY,
    items.filter((item) => item.id !== id),
  );
}

export async function clearImageConversations(): Promise<void> {
  await imageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY);
}
