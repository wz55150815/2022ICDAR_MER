import torch
import cv2
from pathlib import Path
from typing import Dict
import pickle as pkl
import multiprocessing
from torch.utils.data import DataLoader, Dataset


class HYBTr_Dataset(Dataset):
    def __init__(self, params, image_path, label_path, words, is_train=True):
        with open(image_path, 'rb') as f:
            self.images = pkl.load(f)

        with open(label_path, 'rb') as f:
            self.labels = pkl.load(f)

        self.name_list = list(self.labels.keys())
        self.words = words
        self.max_width = params['image_width']
        self.is_train = is_train
        self.params = params
        self.image_height = params['image_height']
        self.image_width = params['image_width']

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):

        name = self.name_list[idx]
        try:
            image = cv2.cvtColor(self.images[name], cv2.COLOR_BGR2GRAY)
        except cv2.error:  # 图像已经经过上面的函数出来了
            image = self.images[name]

        image = torch.Tensor(image) / 255
        if len(image.shape) == 2:
            image = image.unsqueeze(0)

        label = self.labels[name]

        child_words = [item.split()[1] for item in label]
        child_words = self.words.encode(child_words)
        child_words = torch.LongTensor(child_words)
        child_ids = [int(item.split()[0]) for item in label]
        child_ids = torch.LongTensor(child_ids)

        parent_words = [item.split()[3] for item in label]
        parent_words = self.words.encode(parent_words)
        parent_words = torch.LongTensor(parent_words)
        parent_ids = [int(item.split()[2]) for item in label]
        parent_ids = torch.LongTensor(parent_ids)

        # child_words, child_ids, parent_words, parent_ids, struct_label = [], [], [], [], []
        # for line in label.split("\n"):
        #     if line != "":
        #         child_words.append(line.split()[1])
        #         child_ids.append(line.split()[0])
        #         parent_words.append(line.split()[3])
        #         parent_ids.append(line.split()[2])
        #         struct_label.append(line.split()[4:])
        #     else:
        #         break
        #
        # child_words = self.words.encode(child_words)
        # child_words = torch.LongTensor(child_words)
        # child_ids = torch.LongTensor([int(i) for i in child_ids])
        # parent_words = self.words.encode(parent_words)
        # parent_words = torch.LongTensor(parent_words)
        # parent_ids = torch.LongTensor([int(i) for i in parent_ids])

        struct_label = [item.split()[4:] for item in label]
        struct = torch.zeros((len(struct_label), len(struct_label[0]))).long()
        for i in range(len(struct_label)):
            for j in range(len(struct_label[0])):
                struct[i][j] = struct_label[i][j] != 'None'

        label = torch.cat([child_ids.unsqueeze(1), child_words.unsqueeze(1), parent_ids.unsqueeze(1), parent_words.unsqueeze(1), struct], dim=1)

        return image, label

    def collate_fn(self, batch_images):

        max_width, max_height, max_length = 0, 0, 0
        batch, channel = len(batch_images), batch_images[0][0].shape[0]
        proper_items = []
        for item in batch_images:
            if item[0].shape[1] * max_width > self.image_width * self.image_height or item[0].shape[2] * max_height > self.image_width * self.image_height:
                continue
            max_height = item[0].shape[1] if item[0].shape[1] > max_height else max_height
            max_width = item[0].shape[2] if item[0].shape[2] > max_width else max_width
            max_length = item[1].shape[0] if item[1].shape[0] > max_length else max_length
            proper_items.append(item)

        images, image_masks = torch.zeros((len(proper_items), channel, max_height, max_width)), torch.zeros(
            (len(proper_items), 1, max_height, max_width))
        labels, labels_masks = torch.zeros((len(proper_items), max_length, 11)).long(), torch.zeros(
            (len(proper_items), max_length, 2))

        for i in range(len(proper_items)):

            _, h, w = proper_items[i][0].shape
            images[i][:, :h, :w] = proper_items[i][0]
            image_masks[i][:, :h, :w] = 1

            l = proper_items[i][1].shape[0]
            labels[i][:l, :] = proper_items[i][1]
            labels_masks[i][:l, 0] = 1

            for j in range(proper_items[i][1].shape[0]):
                labels_masks[i][j][1] = proper_items[i][1][j][4:].sum() != 0

        return images, image_masks, labels, labels_masks


def get_dataset(params):

    words = tokenizer
    num_gpus = torch.cuda.device_count()
    params['word_num'] = len(words)
    params['struct_num'] = len(tokenizer.struct_ids)
    print(f"training data，images: {params['train_image_path']} labels: {params['train_label_path']}")
    print(f"test data，images: {params['eval_image_path']} labels: {params['eval_label_path']}")
    train_dataset = HYBTr_Dataset(params, params['train_image_path'], params['train_label_path'], words)
    eval_dataset = HYBTr_Dataset(params, params['eval_image_path'], params['eval_label_path'], words)
    # 给每个rank对应的进程分配训练的样本索引
    train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset)
    eval_sampler = torch.utils.data.distributed.DistributedSampler(eval_dataset)
    # 将样本索引每batch_size个元素组成一个list
    train_batch_sampler = torch.utils.data.BatchSampler(
        train_sampler, params['batch_size'], drop_last=True)

    train_loader = DataLoader(train_dataset, batch_sampler=train_batch_sampler, pin_memory=True,
                              num_workers=0, collate_fn=train_dataset.collate_fn)
    eval_loader = DataLoader(eval_dataset, batch_size=params['batch_size'], sampler=eval_sampler, pin_memory=True,
                             num_workers=0, collate_fn=eval_dataset.collate_fn)

    print(f'train dataset: {len(train_dataset)} train steps: {len(train_loader)} '
          f'eval dataset: {len(eval_dataset)} eval steps: {len(eval_loader)}')

    return train_loader, eval_loader


class Words:
    def __init__(self, words_path):
        with open(words_path) as f:
            words = f.readlines()
            print(f'{len(words)} symbols in total')

        self.words_dict = {words[i].strip(): i for i in range(len(words))}
        self.words_index_dict = {i: words[i].strip() for i in range(len(words))}
        self.frac_token = self.words_dict[r"\frac"]
        self.sum_token = self.words_dict[r"\sum"]
        self.right_id = self.words_dict["right"]
        self.sos_id = self.words_dict["<sos>"]
        self.pad_id = self.words_dict["<pad>"]
        self.eos_id = self.words_dict["<eos>"]
        self.struct_id = self.words_dict["struct"]

    def __len__(self):
        return len(self.words_dict)

    def encode(self, labels):
        label_index = [self.words_dict[item] for item in labels]
        return label_index

    def decode(self, label_index):
        label = ' '.join([self.words_index_dict[int(item)] for item in label_index])
        return label

    @property
    def struct_ids(self) -> Dict:
        structs = ("above", "below", "sub", "sup", "L-sup", "inside", "right")
        structs_dict = {x: self.words_dict[x] for x in structs}
        return structs_dict

    @property
    def vocab_size(self):
        return len(self.words_dict)


tokenizer = Words(words_path=r"./data/dictionary.txt")



