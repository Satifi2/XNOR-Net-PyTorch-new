from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import os
import torch
import argparse
import util
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms

from models import nin
from torch.autograd import Variable

def save_state(model, best_acc):
    print('==> Saving model ...')
    # Get script directory and create model_hub there
    script_dir = os.path.dirname(os.path.abspath(__file__))
    model_hub_path = os.path.join(script_dir, 'model_hub')
    os.makedirs(model_hub_path, exist_ok=True)
    
    state = {
            'best_acc': best_acc,
            'state_dict': model.state_dict(),
            }
    # Create a copy of keys to avoid mutation during iteration
    keys = list(state['state_dict'].keys())
    for key in keys:
        if 'module' in key:
            state['state_dict'][key.replace('module.', '')] = \
                    state['state_dict'].pop(key)
    torch.save(state, os.path.join(model_hub_path, 'nin.pth.tar'))

def train(epoch):
    model.train()
    for batch_idx, (data, target) in enumerate(trainloader):
        # process the weights including binarization
        bin_op.binarization()
        
        # forwarding
        data, target = Variable(data.cuda()), Variable(target.cuda())
        optimizer.zero_grad()
        output = model(data)
        
        # backwarding
        loss = criterion(output, target)
        loss.backward()
        
        # restore weights
        bin_op.restore()
        bin_op.updateBinaryGradWeight()
        
        optimizer.step()
        if batch_idx % 100 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\tLR: {}'.format(
                epoch, batch_idx * len(data), len(trainloader.dataset),
                100. * batch_idx / len(trainloader), loss.data.item(),
                optimizer.param_groups[0]['lr']))
    return

def test():
    global best_acc
    model.eval()
    test_loss = 0
    correct = 0
    bin_op.binarization()
    for data, target in testloader:
        data, target = Variable(data.cuda()), Variable(target.cuda())
                                    
        output = model(data)
        test_loss += criterion(output, target).data.item()
        pred = output.data.max(1, keepdim=True)[1]
        correct += pred.eq(target.data.view_as(pred)).cpu().sum()
    bin_op.restore()
    acc = 100. * float(correct) / len(testloader.dataset)

    if acc > best_acc:
        best_acc = acc
        save_state(model, best_acc)
    
    test_loss /= len(testloader.dataset)
    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.2f}%)'.format(
        test_loss * 128., correct, len(testloader.dataset),
        100. * float(correct) / len(testloader.dataset)))
    print('Best Accuracy: {:.2f}%\n'.format(best_acc))
    return

def adjust_learning_rate(optimizer, epoch):
    update_list = [120, 200, 240, 280]
    if epoch in update_list:
        for param_group in optimizer.param_groups:
            param_group['lr'] = param_group['lr'] * 0.1
    return

if __name__=='__main__':
    # prepare the options
    # python XNOR-Net-PyTorch\CIFAR_10\main.py --pretrained XNOR-Net-PyTorch\CIFAR_10\model_hub\nin.pth.tar --evaluat
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpu', action='store_true',
            help='set if only CPU is available')
    parser.add_argument('--data', action='store', default='./data/',
            help='dataset path')
    parser.add_argument('--arch', action='store', default='nin',
            help='the architecture for the network: nin')
    parser.add_argument('--lr', action='store', default='0.01',
            help='the intial learning rate')
    parser.add_argument('--pretrained', action='store', default=None,
            help='the path to the pretrained model')
    parser.add_argument('--evaluate', action='store_true',
            help='evaluate the model')
    args = parser.parse_args()
    print('==> Options:',args)

    # set the seed
    torch.manual_seed(1)
    torch.cuda.manual_seed(1)

    # prepare the data
    transform_train = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])

    transform_test = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])

    trainset = torchvision.datasets.CIFAR10(
        root=args.data, train=True, download=True, transform=transform_train)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=128,
            shuffle=True, num_workers=2)

    testset = torchvision.datasets.CIFAR10(
        root=args.data, train=False, download=True, transform=transform_test)
    testloader = torch.utils.data.DataLoader(testset, batch_size=100,
            shuffle=False, num_workers=2)

    # define classes
    classes = ('plane', 'car', 'bird', 'cat',
            'deer', 'dog', 'frog', 'horse', 'ship', 'truck')

    # define the model
    print('==> building model',args.arch,'...')
    if args.arch == 'nin':
        model = nin.Net()
    else:
        raise Exception(args.arch+' is currently not supported')

    # initialize the model
    if not args.pretrained:
        print('==> Initializing model parameters ...')
        best_acc = 0
        for m in model.modules():
            if isinstance(m, nn.Conv2d):
                m.weight.data.normal_(0, 0.05)
                m.bias.data.zero_()
    else:
        print('==> Load pretrained model form', args.pretrained, '...')
        pretrained_model = torch.load(args.pretrained)
        best_acc = pretrained_model['best_acc']
        model.load_state_dict(pretrained_model['state_dict'])

    if not args.cpu:
        model.cuda()
        model = torch.nn.DataParallel(model, device_ids=range(torch.cuda.device_count()))
    print(model)

    # define solver and criterion
    base_lr = float(args.lr)
    param_dict = dict(model.named_parameters())
    params = []

    for key, value in param_dict.items():
        params += [{'params':[value], 'lr': base_lr,
            'weight_decay':0.00001}]

    optimizer = optim.Adam(params, lr=0.10,weight_decay=0.00001)
    criterion = nn.CrossEntropyLoss()

    # define the binarization operator
    bin_op = util.BinOp(model)

    # do the evaluation if specified
    if args.evaluate:
        test()
        exit(0)

    # start training
    for epoch in range(1, 320):
        adjust_learning_rate(optimizer, epoch)
        train(epoch)
        test()
